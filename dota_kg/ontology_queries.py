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


_FIELD_NAMES = {
    'hero': 'Герой', 'enemy': 'Противник', 'name': 'Название', 'score': 'Балл',
    'source': 'Источник эффекта', 'subject': 'Способность или предмет',
    'ability': 'Способность', 'mechanic': 'Механика', 'rule': 'Правило',
    'weight': 'Вес правила', 'targetMechanic': 'Механика противника',
    'targetFact': 'Основание', 'evidence': 'Объяснение', 'targetEvidence': 'Объяснение цели',
    'condition': 'Условие', 'requiredItem': 'Необходимый предмет',
    'kind': 'Тип условия', 'scope': 'Участник', 'effect': 'Эффект',
    'description': 'Описание', 'note': 'Примечание',
}
_VALUE_NAMES = {
    'gameplay': 'Игровое условие', 'has-item': 'Наличие предмета',
    'has-mechanic': 'Наличие механики', 'source': 'Наш герой', 'target': 'Противник',
    'self': 'На себе', 'ally': 'На союзнике', 'enemy': 'На противнике',
    'both': 'На себе или союзнике', 'area': 'В области',
}


def _write_query_text(result, output, graph):
    """Print independent wrapped records, retaining every projected binding."""
    from textwrap import fill
    from .localization import russian_text

    names = {}
    def cell(value, variable):
        if value is None:
            return 'Нет значения'
        if isinstance(value, URIRef):
            if value not in names:
                labels = [label for label in graph.objects(value, RDFS.label) if str(label)]
                labels.sort(key=lambda label: (
                    not (getattr(label, 'language', None) or '').lower().startswith('ru'), str(label)))
                local_name = str(value).rstrip('/#').rsplit('#', 1)[-1].rsplit('/', 1)[-1].rsplit(':', 1)[-1]
                names[value] = str(labels[0]) if labels else local_name
            value = names[value]
        if isinstance(value, Literal) and isinstance(value.toPython(), bool):
            return 'Да' if value.toPython() else 'Нет'
        text = russian_text(value) if isinstance(value, Literal) else str(value)
        if variable in {'evidence', 'targetEvidence', 'condition', 'rule'}:
            text = russian_text(text)
        if variable in {'scope', 'kind'}:
            text = _VALUE_NAMES.get(text, text)
        return ' '.join(text.split())

    if not result.bindings:
        print('В размеченных данных подходящих вариантов не найдено.', file=output)
        return 0
    answer_variable = next((v for v in result.vars if str(v) == 'answer'), None)
    if answer_variable is not None:
        answers = [row.get(answer_variable) for row in result.bindings]
        if any(value is None for value in answers):
            raise RuntimeError('Запрос оставил ?answer без значения')
        for answer in answers:
            print(fill('- ' + russian_text(answer), width=88, subsequent_indent='  '), file=output)
        return len(answers)
    for index, binding in enumerate(result.bindings, 1):
        if index > 1:
            print(file=output)
        print(f'{index}.', file=output)
        displayed = set()
        for variable in result.vars:
            value = binding.get(variable)
            key = str(variable)
            # Do not repeat a projected label or the same translated explanation.
            text = cell(value, key)
            if key in {'name', 'ability', 'targetEvidence'} and text in displayed:
                continue
            label = _FIELD_NAMES.get(key, key)
            print(fill(f'{label}: {text}', width=88, initial_indent='   ',
                       subsequent_indent='   '), file=output)
            displayed.add(text)
    return len(result.bindings)


def run_query(query_file: Path, *, directory: Path,
              output_format: str = "text", output=None, progress=None,
              bindings=None, own_items=(), enemy_items=(), level=18) -> int:
    """Execute a saved query on the local game ontology."""
    from .ontology import load_ontology, turtle_bytes

    output = sys.stdout if output is None else output
    progress = sys.stderr if progress is None else progress
    path = Path(query_file).expanduser()
    if path.suffix.lower() not in (".rq", ".sparql"):
        raise RuntimeError("Откройте и сохраните SPARQL-файл с расширением .rq или .sparql")
    if output_format not in ("text", "json", "csv", "turtle"):
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
    if graph_query and output_format not in ("text", "turtle"):
        raise RuntimeError("Для CONSTRUCT и DESCRIBE используйте формат turtle")
    if not graph_query and output_format == "turtle":
        raise RuntimeError("Формат turtle предназначен для CONSTRUCT и DESCRIBE")
    if prepared.algebra.name == "AskQuery" and output_format == "csv":
        raise RuntimeError("Для ASK используйте формат text или json")
    started = time.perf_counter()
    print("Загрузка игровой модели...", file=progress, flush=True)
    try:
        graph = load_ontology(directory, cache=True)
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
            print("Да." if result.askAnswer else "Нет.", file=output)
            count = 1
        else:
            count = _write_query_text(result, output, query_graph)
    except Exception as error:
        raise RuntimeError(f"Не удалось выполнить SPARQL-запрос: {error}") from error
    print(f"Результат: {count}. Время: {time.perf_counter() - started:.2f} с.", file=progress, flush=True)
    return count
