"""Run arbitrary local SPARQL files with typed results."""
from __future__ import annotations

import csv
from contextlib import ExitStack
import re
from pathlib import Path
import sys
import time
from rdflib import BNode, Literal, RDFS, URIRef
from rdflib.util import from_n3
from rdflib.plugins.sparql import prepareQuery



def _check_local_query(value):
    """Reject clauses that would fetch data outside the loaded local graph."""
    if isinstance(value, dict):
        if (getattr(value, "name", None) == "ServiceGraphPattern"
                or ("datasetClause" in value and value["datasetClause"])):
            raise RuntimeError("В локальном режиме FROM и SERVICE не поддерживаются")
        for child in value.values():
            _check_local_query(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _check_local_query(child)


def _write_query_table(result, output, graph):
    names = {}

    def cell(value):
        if value is None:
            return ""
        if isinstance(value, URIRef):
            if value not in names:
                labels = [label for label in graph.objects(value, RDFS.label) if str(label)]
                labels.sort(key=lambda label: (
                    not (getattr(label, "language", None) or "").lower().startswith("ru"), str(label)))
                local_name = str(value).rstrip("/#").rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1]
                names[value] = str(labels[0]) if labels else local_name
            value = names[value]
        return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

    headers = [str(variable) for variable in result.vars]
    rows = [[cell(binding.get(variable)) for variable in result.vars]
            for binding in result.bindings]
    widths = [max([len(header), *(len(row[index]) for row in rows)])
              for index, header in enumerate(headers)]
    render = lambda row: " | ".join(value.ljust(width) for value, width in zip(row, widths))
    print(render(headers), file=output)
    print("-+-".join("-" * width for width in widths), file=output)
    for row in rows:
        print(render(row), file=output)
    return len(rows)


def run_query(query_file: Path, *, directory: Path,
              output_format: str = "table", output=None, progress=None,
              bindings=None, own_items=(), enemy_items=(), level=18) -> int:
    """Execute a saved query on the local game ontology."""
    from .ontology import load_ontology, turtle_bytes

    output = sys.stdout if output is None else output
    progress = sys.stderr if progress is None else progress
    path = Path(query_file).expanduser()
    if path.suffix.lower() not in (".rq", ".sparql"):
        raise RuntimeError("Откройте и сохраните SPARQL-файл с расширением .rq или .sparql")
    if output_format not in ("text", "table", "json", "csv", "turtle"):
        raise RuntimeError(f"Неизвестный формат результата: {output_format}")
    try:
        text = path.read_text(encoding="utf-8-sig")
        prepared = prepareQuery(text)
    except OSError as error:
        raise RuntimeError(f"Не удалось прочитать запрос {path}: {error}") from error
    except Exception as error:
        raise RuntimeError(f"Неверный SPARQL-запрос в {path}: {error}") from error
    _check_local_query(prepared.algebra)
    graph_query = prepared.algebra.name in ("ConstructQuery", "DescribeQuery")
    if graph_query and output_format not in ("table", "turtle"):
        raise RuntimeError("Для CONSTRUCT и DESCRIBE используйте формат turtle")
    if not graph_query and output_format == "turtle":
        raise RuntimeError("Формат turtle предназначен для CONSTRUCT и DESCRIBE")
    if prepared.algebra.name == "AskQuery" and output_format == "csv":
        raise RuntimeError("Для ASK используйте формат table или json")
    if output_format == "text" and (
            prepared.algebra.name != "SelectQuery"
            or "answer" not in {str(v) for v in prepared.algebra.PV}):
        raise RuntimeError("Для формата text нужен SELECT с переменной ?answer")
    started = time.perf_counter()
    print("Загрузка игровой модели...", file=progress, flush=True)
    try:
        graph = load_ontology(directory)
        print(f"Загружено {len(graph):,} троек. Выполняется {path.name}...", file=progress, flush=True)
        from .questions import QuestionParser
        from .ontology_schema import KG
        from .ranking_sparql import ranking_functions, ranking_query
        resolver = QuestionParser(graph) if bindings or own_items or enemy_items else None
        bound = {}
        entries = bindings.items() if isinstance(bindings, dict) else (
            entry.split('=', 1) if '=' in entry else (entry, None) for entry in (bindings or ()))
        for variable, value in entries:
            variable = variable.lstrip('?')
            if not re.fullmatch(r'[A-Za-z_]\w*', variable) or value is None:
                raise ValueError('Параметр задается как --bind имя=значение')
            if hasattr(value, 'n3'):
                term = value
            elif str(value).startswith(('<', '"')):
                term = from_n3(str(value))
            elif re.fullmatch(r'-?\d+', str(value)):
                term = Literal(int(value))
            elif re.fullmatch(r'-?\d+\.\d+', str(value)):
                term = Literal(float(value))
            elif str(value) in ('true', 'false'):
                term = Literal(str(value) == 'true')
            elif str(value).startswith(('https://', 'http://', 'urn:')):
                term = URIRef(value)
            else:
                try:
                    term = resolver.resolve_entity(value, KG.Hero if variable in {'hero', 'enemy'} else
                                                   KG.Item if variable == 'item' else None)
                except ValueError:
                    if variable in {'hero', 'enemy', 'subject', 'item'}:
                        raise
                    term = Literal(str(value))
            bound[variable] = term
        with ExitStack() as stack:
            query_graph = graph
            if ranking_functions(prepared.algebra):
                own = [resolver.resolve_entity(v, KG.Item) for v in own_items]
                opposing = [resolver.resolve_entity(v, KG.Item) for v in enemy_items]
                query_graph = stack.enter_context(ranking_query(graph, own_items=own,
                    enemy_items=opposing, level=level, actor=bound.get('hero'),
                    position=bound['position'].toPython() if 'position' in bound else None,
                    phase=str(bound['phase']) if 'phase' in bound else None))
            result = query_graph.query(prepared, initBindings=bound)
            if result.type == 'SELECT':
                result.bindings = list(result.bindings)
        if graph_query:
            output.write(turtle_bytes(result.graph).decode("utf-8"))
            count = len(result.graph)
        elif output_format == "text":
            answer_variable = next(v for v in result.vars if str(v) == "answer")
            answers = [row.get(answer_variable) for row in result.bindings]
            if any(value is None for value in answers):
                raise RuntimeError("Запрос оставил ?answer без значения")
            if not answers:
                print("В размеченных данных подходящих вариантов не найдено.", file=output)
            for answer in answers:
                print(f"- {answer}", file=output)
            count = len(answers)
        elif output_format == "json":
            output.write(result.serialize(format="json").decode("utf-8"))
            count = 1 if result.type == "ASK" else len(result)
            output.write("\n")
        elif output_format == "csv":
            writer = csv.writer(output)
            writer.writerow(str(variable) for variable in result.vars)
            for binding in result.bindings:
                values = [binding.get(variable) for variable in result.vars]
                writer.writerow(value.n3() if isinstance(value, BNode) else value for value in values)
            count = len(result)
        elif result.type == "ASK":
            print("true" if result.askAnswer else "false", file=output)
            count = 1
        else:
            count = _write_query_table(result, output, query_graph)
    except Exception as error:
        raise RuntimeError(f"Не удалось выполнить SPARQL-запрос: {error}") from error
    print(f"Результат: {count}. Время: {time.perf_counter() - started:.2f} с.", file=progress, flush=True)
    return count
