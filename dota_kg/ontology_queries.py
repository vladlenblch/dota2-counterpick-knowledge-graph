"""Run arbitrary local SPARQL files with typed results."""
from __future__ import annotations

import csv
from pathlib import Path
import sys
import time
from rdflib import BNode
from rdflib.plugins.sparql import prepareQuery



# Internal competency checks stay available without bundled query files.

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


def _write_query_table(result, output):
    def cell(value):
        if value is None:
            return ""
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
              output_format: str = "table", output=None, progress=None) -> int:
    """Execute a saved query on the local game ontology."""
    from .ontology import load_ontology, turtle_bytes

    output = sys.stdout if output is None else output
    progress = sys.stderr if progress is None else progress
    path = Path(query_file).expanduser()
    if path.suffix.lower() not in (".rq", ".sparql"):
        raise RuntimeError("Откройте и сохраните SPARQL-файл с расширением .rq или .sparql")
    if output_format not in ("table", "json", "csv", "turtle"):
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
    started = time.perf_counter()
    print("Загрузка игровой модели...", file=progress, flush=True)
    try:
        graph = load_ontology(directory)
        print(f"Загружено {len(graph):,} троек. Выполняется {path.name}...", file=progress, flush=True)
        result = graph.query(prepared)
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
            print("true" if result.askAnswer else "false", file=output)
            count = 1
        else:
            count = _write_query_table(result, output)
    except Exception as error:
        raise RuntimeError(f"Не удалось выполнить SPARQL-запрос: {error}") from error
    print(f"Результат: {count}. Время: {time.perf_counter() - started:.2f} с.", file=progress, flush=True)
    return count
