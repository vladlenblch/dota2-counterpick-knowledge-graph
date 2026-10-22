"""Run local SPARQL files and verify competency queries against source data."""
from __future__ import annotations

import csv
from pathlib import Path
import sys
import time
import pyarrow.parquet as pq
from rdflib import BNode
from rdflib.plugins.sparql import prepareQuery

from .ontology_schema import KG, evidence_text


# Internal competency checks stay available without bundled query files.
COMPETENCY_QUERIES = {
    "inventory": """PREFIX kg: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/ontology#>
SELECT ?class (COUNT(?entity) AS ?count)
WHERE {
  VALUES ?class { kg:Hero kg:Ability kg:Item kg:Feature kg:FeatureObservation
                  kg:MatchupObservation kg:ItemUsageObservation kg:FeatureValue }
  ?entity a ?class .
}
GROUP BY ?class
ORDER BY ?class
""",
    "rubick_features": """PREFIX kg: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/ontology#>
PREFIX hero: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/resource/hero/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?code ?value ?evidence ?abilityName
WHERE {
  ?observation a kg:FeatureObservation ;
    kg:hero hero:86 ; kg:feature ?feature ; kg:value ?value ; kg:evidence ?evidence .
  ?feature kg:identifier ?code .
  OPTIONAL { ?observation kg:supportedByAbility ?ability . ?ability rdfs:label ?abilityName . }
}
ORDER BY ?code
""",
    "forced_movement": """PREFIX kg: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/ontology#>
PREFIX feature: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/resource/feature/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?id ?name ?evidence
WHERE {
  ?observation a kg:FeatureObservation ;
    kg:feature feature:FORCED_MOVEMENT ; kg:hero ?hero ; kg:value ?value ; kg:evidence ?evidence .
  FILTER (?value = 1.0e0)
  ?hero kg:identifier ?id ; rdfs:label ?name .
}
ORDER BY ?id
""",
    "rubick_late_items": """PREFIX kg: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/ontology#>
PREFIX hero: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/resource/hero/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?id ?name ?uses
WHERE {
  ?observation a kg:ItemUsageObservation ; kg:hero hero:86 ;
    kg:phase ?phase ; kg:item ?item ; kg:usageCount ?uses .
  FILTER (?phase = "late")
  ?item kg:identifier ?id ; rdfs:label ?name .
}
ORDER BY DESC(?uses) ?name ?id
LIMIT 5
""",
}


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


def competency_checks(graph, root: Path):
    """Compare four built-in SPARQL results with the project datasets."""
    root = Path(root)
    read = lambda name: pq.read_table(root / f"data/final/{name}.parquet").to_pylist()
    heroes = {r["hero_id"]: r["display_name"] for r in read("heroes")}
    items = {r["item_id"]: r["display_name"] for r in read("items")}
    features = read("hero_features")
    inventory = {
        "Hero": len(heroes), "Ability": len(read("abilities")), "Item": len(items),
        "Feature": len({r["feature"] for r in features}), "FeatureObservation": len(features),
        "MatchupObservation": len(read("matchups")), "ItemUsageObservation": len(read("hero_items")),
        "FeatureValue": sum(len(row) - 1 for row in read("hero_feature_matrix")),
    }
    rubick = sorted((r["feature"], r["value"], evidence_text(r["evidence"])) for r in features if r["hero_id"] == 86)
    forced = sorted((r["hero_id"], heroes[r["hero_id"]], evidence_text(r["evidence"]))
                    for r in features if r["feature"] == "FORCED_MOVEMENT" and r["value"] == 1)
    top_items = sorted((r for r in read("hero_items") if r["hero_id"] == 86 and r["phase"] == "late"),
                       key=lambda r: (-r["usage_count"], items[r["item_id"]], r["item_id"]))[:5]
    expected = {
        "inventory": sorted((str(KG[key]), count) for key, count in inventory.items()),
        "rubick_features": rubick,
        "forced_movement": forced,
        "rubick_late_items": [(r["item_id"], items[r["item_id"]], r["usage_count"]) for r in top_items],
    }
    results = {}
    for name, original in expected.items():
        query = COMPETENCY_QUERIES[name]
        started = time.perf_counter()
        print(f"SPARQL {name}: ", end="", file=sys.stderr, flush=True)
        actual = [tuple(value.toPython() if value is not None else None for value in row)
                  for row in graph.query(query)]
        if name == "rubick_features":
            comparable = [row[:3] for row in actual]
        else: comparable = actual
        if comparable != original:
            raise ValueError(f"SPARQL {name} differs from Parquet: {comparable!r} != {original!r}")
        print(f"{len(actual)} rows, {time.perf_counter() - started:.3f} s", file=sys.stderr, flush=True)
        results[name] = {"passed": True, "rows": len(actual), "result": actual}
    return results
