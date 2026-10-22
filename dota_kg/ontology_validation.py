"""Decode the saved ontology and compare every source byte, cell and observation."""
from __future__ import annotations

import base64
import csv
import io
import json
import struct
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq
from rdflib import BNode, Graph, Literal, RDF, RDFS, OWL, XSD, URIRef
from pyshacl import validate

from .ontology_schema import (
    KG, RES, PROV, SH, TABLES, JSON_COLUMNS, FEATURE_LABELS, NUMERIC_FEATURES,
    SEMANTIC_FIELDS, STRUCTURED_PROPERTIES, column_predicate, structured_predicate, bind,
)


def one(graph, subject, predicate, *, optional=False):
    values = list(graph.objects(subject, predicate))
    if optional and not values:
        return None
    if len(values) != 1:
        raise ValueError(f"Expected one {predicate} on {subject}, found {len(values)}")
    return values[0]


def check_scalar(node, expected, datatype):
    if not isinstance(node, Literal) or node.datatype != datatype:
        raise ValueError(f"Datatype/value mismatch: {node!r}, expected {datatype}")
    actual = node.toPython()
    if type(actual) is not type(expected):
        raise ValueError(f"Python type mismatch: {actual!r} vs {expected!r}")
    if isinstance(expected, float):
        if struct.pack(">d", actual) != struct.pack(">d", expected):
            raise ValueError(f"Float64 changed: {actual!r} vs {expected!r}")
    elif actual != expected:
        raise ValueError(f"Value changed: {actual!r} vs {expected!r}")


class JsonDecoder:
    def __init__(self, graph):
        self.graph = graph
        self.cache = {}
        self.active = set()

    @staticmethod
    def scalar(node):
        if not isinstance(node, Literal):
            raise ValueError(f"JSON scalar must be a literal: {node!r}")
        value = node.toPython()
        datatype = {str: XSD.string, bool: XSD.boolean, int: XSD.integer, float: XSD.double}.get(type(value))
        if datatype is None:
            raise ValueError(f"Unsupported JSON scalar: {node!r}")
        check_scalar(node, value, datatype)
        return value

    def entry_value(self, entry):
        scalar = list(self.graph.objects(entry, KG.jsonScalar))
        nested = list(self.graph.objects(entry, KG.jsonChild))
        if len(scalar) + len(nested) != 1:
            raise ValueError(f"JSON entry must have exactly one value: {entry}")
        return self.decode(nested[0]) if nested else self.scalar(scalar[0])

    def decode(self, node):
        if node in self.cache:
            return self.cache[node]
        if node in self.active:
            raise ValueError("JSON graph contains a cycle")
        if (node, RDF.type, KG.JsonValue) not in self.graph:
            raise ValueError(f"Missing JsonValue type: {node}")
        self.active.add(node)
        kind_node = one(self.graph, node, KG.jsonKind)
        kind = self.scalar(kind_node)
        if type(kind) is not str or kind not in {"object", "array", "scalar", "null"}:
            raise ValueError(f"Invalid JSON kind: {kind!r}")
        entries = list(self.graph.objects(node, KG.hasEntry))
        if kind == "null":
            if entries or list(self.graph.objects(node, KG.jsonScalar)) or list(self.graph.objects(node, KG.entryCount)):
                raise ValueError("JSON null contains a value")
            result = None
        elif kind == "scalar":
            if entries or list(self.graph.objects(node, KG.entryCount)):
                raise ValueError("JSON scalar contains entries")
            result = self.scalar(one(self.graph, node, KG.jsonScalar))
        else:
            if list(self.graph.objects(node, KG.jsonScalar)):
                raise ValueError("JSON container has a scalar value")
            count_node = one(self.graph, node, KG.entryCount)
            count = self.scalar(count_node)
            if type(count) is not int or count < 0 or count != len(entries):
                raise ValueError("JSON container length changed")
            if kind == "object":
                result = {}
                for entry in entries:
                    key = self.scalar(one(self.graph, entry, KG.entryKey))
                    if type(key) is not str or list(self.graph.objects(entry, KG.entryIndex)):
                        raise ValueError("Invalid JSON object key")
                    if key in result:
                        raise ValueError("Duplicate JSON object key")
                    result[key] = self.entry_value(entry)
            else:
                indexed = {}
                for entry in entries:
                    index = self.scalar(one(self.graph, entry, KG.entryIndex))
                    if type(index) is not int or index in indexed or list(self.graph.objects(entry, KG.entryKey)):
                        raise ValueError("Duplicate or noninteger JSON index")
                    indexed[index] = self.entry_value(entry)
                if set(indexed) != set(range(count)):
                    raise ValueError("Missing JSON array positions")
                result = [indexed[i] for i in range(count)]
        self.active.remove(node)
        self.cache[node] = result
        return result


def same_json(expected, actual):
    """Do not conflate false, integer zero, float zero, null and empty values."""
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, dict):
        return expected.keys() == actual.keys() and all(same_json(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return len(expected) == len(actual) and all(same_json(a, b) for a, b in zip(expected, actual))
    if isinstance(expected, float):
        return struct.pack(">d", expected) == struct.pack(">d", actual)
    return expected == actual


def make_shapes():
    graph = bind(Graph())
    graph.bind("sh", SH)
    prop = lambda p, t, lo=1, hi=1, lower=None, upper=None: (p, t, lo, hi, lower, upper)
    common = [prop(KG.snapshot, KG.DatasetSnapshot),
              prop(PROV.wasDerivedFrom, KG.SourceArtifact, hi=None)]
    rules = {cls: list(common) for cls, _ in TABLES.values()}
    for cls in ("Hero", "Ability", "Item"):
        rules[cls] = [prop(KG.identifier, XSD.integer)]
    rules["HeroSnapshot"] += [prop(KG.hero, KG.Hero)]
    rules["AbilitySnapshot"] += [prop(KG.hero, KG.Hero), prop(KG.ability, KG.Ability)]
    rules["ItemSnapshot"] += [prop(KG.item, KG.Item)]
    rules["Feature"] = [prop(KG.identifier, XSD.string), prop(KG.valueKind, XSD.string), prop(KG.unit, XSD.string)]
    rules["MatchupObservation"] += [
        prop(KG.hero, KG.Hero), prop(KG.opponent, KG.Hero),
        prop(KG.matches, XSD.integer, lower=1), prop(KG.wins, XSD.integer, lo=0, lower=0),
        prop(KG.winRate, XSD.double, lower=0, upper=1),
        prop(KG.normalizedAdvantage, XSD.double, lo=0, lower=-1, upper=1),
        prop(KG.source, KG.DataSource, lo=0), prop(KG.collectedAt, XSD.dateTime, lo=0)]
    rules["FeatureObservation"] += [prop(KG.hero, KG.Hero), prop(KG.feature, KG.Feature),
        prop(KG.value, XSD.double), prop(KG.evidence, XSD.string), prop(KG.method, XSD.string)]
    rules["FeatureVector"] += [prop(KG.hero, KG.Hero), prop(KG.matrixValues, KG.JsonValue)]
    rules["ItemUsageObservation"] += [prop(KG.hero, KG.Hero), prop(KG.item, KG.Item),
        prop(KG.phase, XSD.string), prop(KG.usageCount, XSD.integer, lower=0)]
    rules["HeroStatisticsObservation"] += [prop(KG.hero, KG.Hero),
        prop(KG.matches, XSD.integer, lower=0), prop(KG.wins, XSD.integer, lower=0),
        prop(KG.winRate, XSD.double, lo=0, lower=0, upper=1)]
    rules["RankBracketObservation"] = [prop(KG.hero, KG.Hero), prop(KG.snapshot, KG.DatasetSnapshot),
        prop(KG.rankBracket, XSD.string), prop(KG.sourceIndex, XSD.integer, lower=0),
        prop(KG.matches, XSD.integer, lower=0), prop(KG.wins, XSD.integer, lower=0),
        prop(KG.winRate, XSD.double, lo=0, lower=0, upper=1)]
    rules["Parameter"] = [prop(KG.parameterName, XSD.string), prop(KG.sourceIndex, XSD.integer, lower=0),
                           prop(KG.parameterData, KG.JsonValue)]
    for cls, properties in rules.items():
        node = KG[cls + "Shape"]
        graph.add((node, RDF.type, SH.NodeShape))
        graph.add((node, SH.targetClass, KG[cls]))
        for index, (path, kind, minimum, maximum, lower, upper) in enumerate(properties):
            constraint = BNode(cls + "-shape-" + str(index))
            graph.add((node, SH.property, constraint))
            graph.add((constraint, SH.path, path))
            graph.add((constraint, SH.minCount, Literal(minimum)))
            if maximum is not None:
                graph.add((constraint, SH.maxCount, Literal(maximum)))
            if kind:
                predicate = SH.datatype if str(kind).startswith(str(XSD)) else SH["class"]
                graph.add((constraint, predicate, kind))
            if lower is not None:
                graph.add((constraint, SH.minInclusive, Literal(lower)))
            if upper is not None:
                graph.add((constraint, SH.maxInclusive, Literal(upper)))
    return graph


def _package_structure(graph, output):
    """Inspect the existing union and small entry/schema files without loading the data twice."""
    ns = {"c": "urn:oasis:names:tc:entity:xmlns:xml:catalog"}
    entries = ET.parse(output / "catalog-v001.xml").findall("c:uri", ns)
    mapping = {entry.attrib["name"]: entry.attrib["uri"] for entry in entries}
    if len(mapping) != len(entries) or len(set(mapping.values())) != len(mapping):
        raise ValueError("Duplicate ontology catalog mapping")
    for filename in mapping.values():
        path = (output / filename).resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file():
            raise ValueError("Ontology catalog must resolve to existing bundled files")
    main = Graph().parse(output / "game.ttl", format="turtle")
    complete = Graph().parse(output / "dota2.ttl", format="turtle")
    core_schema = Graph().parse(output / "schema.ttl", format="turtle")
    archive_schema = Graph().parse(output / "archive-schema.ttl", format="turtle")
    roots = {}
    for name, entry_graph in (("main", main), ("full", complete)):
        ontology = list(entry_graph.subjects(RDF.type, OWL.Ontology))
        if len(ontology) != 1:
            raise ValueError(f"Expected one ontology in {name} entrypoint")
        roots[name] = ontology[0]
    def closure(start):
        reached = set()
        pending = [start]
        while pending:
            current = pending.pop()
            if current in reached:
                continue
            reached.add(current)
            if (current, RDF.type, OWL.Ontology) not in graph:
                raise ValueError(f"Missing imported ontology: {current}")
            for imported in graph.objects(current, OWL.imports):
                if str(imported) not in mapping:
                    raise ValueError(f"Unmapped ontology import: {imported}")
                pending.append(imported)
        return reached
    closures = {name: closure(node) for name, node in roots.items()}
    expected_main = {"game.ttl", "schema.ttl", "data-entities.ttl", "data-features.ttl", "data-statistics.ttl"}
    expected_full = expected_main | {"dota2.ttl", "archive-schema.ttl", "archive-valve.ttl", "archive-other.ttl"}
    files = {name: {mapping.get(str(node), "game.ttl" if node == roots["main"] else "dota2.ttl")
                    for node in nodes} for name, nodes in closures.items()}
    if files["main"] != expected_main or files["full"] != expected_full:
        raise ValueError(f"Unexpected main/full import closure: {files}")
    all_schema = Graph()
    for schema in (core_schema, archive_schema):
        all_schema += schema
    class_sets = {
        "main": {node for node in core_schema.subjects(RDF.type, OWL.Class) if isinstance(node, URIRef)},
        "full": {node for node in all_schema.subjects(RDF.type, OWL.Class) if isinstance(node, URIRef)},
    }
    actual_classes = {node for node in graph.subjects(RDF.type, OWL.Class) if isinstance(node, URIRef)}
    if actual_classes != class_sets["full"]:
        raise ValueError("Loaded classes differ from the canonical schemas")
    return {"imports": {name: sorted(value) for name, value in files.items()},
            "named_classes": {name: len(value) for name, value in class_sets.items()}}


def validate_export(root: Path, output: Path):
    from .ontology import sha, arrow_datatype, entity, load_ontology
    root, output = Path(root), Path(output)
    print("Чтение RDF-пакета...", file=sys.stderr, flush=True)
    graph = load_ontology(output)
    print(f"Прочитано {len(graph)} троек. Проверка исходных файлов...", file=sys.stderr, flush=True)
    decoder = JsonDecoder(graph)
    checks, errors = [], []
    report = {"conforms": False, "ontology": "ontology/dota2.ttl", "triples": len(graph),
              "tables": {}, "checks": checks, "errors": errors}
    try:
        report["package_structure"] = _package_structure(graph, output)
        snapshots = list(graph.subjects(RDF.type, KG.DatasetSnapshot))
        if len(snapshots) != 1:
            raise ValueError("Expected exactly one source snapshot")
        snapshot = snapshots[0]
        report["snapshot_sha256"] = str(one(graph, snapshot, KG.sha256))
        expected_paths = sorted(p for p in (root / "data").rglob("*") if p.is_file()
                                and not any(part.startswith(".") for part in p.relative_to(root).parts))
        artifacts = {}
        for artifact in graph.subjects(RDF.type, KG.SourceArtifact):
            path = str(one(graph, artifact, KG.relativePath))
            if path in artifacts:
                raise ValueError("Duplicate source artifact path")
            artifacts[path] = artifact
        if set(artifacts) != {p.relative_to(root).as_posix() for p in expected_paths}:
            raise ValueError("Source file inventory differs")
        inventory = [(p.relative_to(root).as_posix(), sha(p.read_bytes())) for p in expected_paths]
        fingerprint = sha(json.dumps(inventory, separators=(",", ":")).encode())
        if str(snapshot) != str(RES[f"snapshot/{fingerprint}"]) or report["snapshot_sha256"] != fingerprint:
            raise ValueError("Snapshot identity differs from input file digests")
        if set(graph.objects(snapshot, KG.hasArtifact)) != set(artifacts.values()):
            raise ValueError("Snapshot artifact links differ")
        source_bytes, structured_documents = 0, 0
        for path in expected_paths:
            relative = path.relative_to(root).as_posix()
            artifact, expected = artifacts[relative], path.read_bytes()
            raw = one(graph, artifact, KG.rawText, optional=True)
            binary = one(graph, artifact, KG.binaryContent, optional=True)
            if (raw is None) == (binary is None):
                raise ValueError("Source artifact must have one payload")
            if raw is not None and raw.datatype != XSD.string:
                raise ValueError("Source text datatype changed")
            if binary is not None:
                if binary.datatype != XSD.string or str(one(graph, artifact, KG.contentEncoding)) != "base64":
                    raise ValueError("Binary payload encoding changed")
            restored = str(raw).encode("utf-8") if raw is not None else base64.b64decode(str(binary), validate=True)
            if restored != expected:
                raise ValueError(f"Source bytes changed: {relative}")
            if str(one(graph, artifact, KG.sha256)) != sha(restored):
                raise ValueError(f"Bad SHA256: {relative}")
            check_scalar(one(graph, artifact, KG.byteLength), len(expected), XSD.integer)
            media = {".json": "application/json", ".csv": "text/csv",
                     ".parquet": "application/vnd.apache.parquet"}.get(path.suffix, "application/octet-stream")
            check_scalar(one(graph, artifact, KG.mediaType), media, XSD.string)
            source_bytes += len(expected)
            if path.suffix in (".json", ".csv"):
                original = (json.loads(expected) if path.suffix == ".json" else
                            list(csv.DictReader(io.StringIO(expected.decode("utf-8")))))
                actual = decoder.decode(one(graph, artifact, KG.jsonRoot))
                if not same_json(original, actual):
                    raise ValueError(f"Structured source differs: {relative}")
                structured_documents += 1
            source = path.relative_to(root).parts[2] if relative.startswith("data/raw/") else None
            actual_source = one(graph, artifact, KG.source, optional=source is None)
            if actual_source != (entity("source", source) if source else None):
                raise ValueError("Artifact source changed")
        report.update({"source_files": len(artifacts), "source_bytes_verified": source_bytes,
                       "structured_documents_verified": structured_documents})
        checks.append("All source files reconstructed byte-for-byte; SHA256 and JSON/CSV structures match")
        print(f"Проверены все {len(artifacts)} исходных файлов.", file=sys.stderr, flush=True)
        rows_by_table, records_by_table = {}, {}
        cells, nulls, json_fields = 0, 0, 0
        matrix_order = None
        for table_name, (class_name, _) in TABLES.items():
            table = pq.read_table(root / "data/final" / f"{table_name}.parquet")
            rows = table.to_pylist()
            tables = [node for node in graph.objects(snapshot, KG.hasTable)
                      if str(one(graph, node, RDFS.label)) == table_name]
            if len(tables) != 1:
                raise ValueError(f"Bad table count: {table_name}")
            table_node = tables[0]
            check_scalar(one(graph, table_node, KG.rowCount), table.num_rows, XSD.integer)
            check_scalar(one(graph, table_node, KG.columnCount), table.num_columns, XSD.integer)
            columns = sorted(graph.objects(table_node, KG.hasColumn),
                             key=lambda c: one(graph, c, KG.columnIndex).toPython())
            if len(columns) != table.num_columns:
                raise ValueError("Column count changed")
            predicates = []
            for column_index, (field, column) in enumerate(zip(table.schema, columns)):
                check_scalar(one(graph, column, KG.columnIndex), column_index, XSD.integer)
                check_scalar(one(graph, column, KG.columnName), field.name, XSD.string)
                check_scalar(one(graph, column, KG.arrowType), str(field.type), XSD.string)
                check_scalar(one(graph, column, KG.nullable), field.nullable, XSD.boolean)
                predicate = one(graph, column, KG.columnPredicate)
                if predicate != column_predicate(table_name, field.name):
                    raise ValueError("Column predicate mapping changed")
                predicates.append(predicate)
                position = one(graph, column, KG.valueIndex, optional=not (table_name == "hero_feature_matrix" and field.name != "hero_id"))
                expected_position = column_index - 1 if table_name == "hero_feature_matrix" and field.name != "hero_id" else None
                if expected_position is None:
                    if position is not None:
                        raise ValueError("Unexpected matrix value index")
                else:
                    check_scalar(position, expected_position, XSD.integer)
            if table_name == "hero_feature_matrix":
                matrix_order = table.column_names[1:]
                if table.column_names[0] != "hero_id" or not same_json(matrix_order, decoder.decode(one(graph, snapshot, KG.matrixFeatureOrder))):
                    raise ValueError("Matrix source feature order changed")
            records = sorted(graph.objects(table_node, KG.hasRow),
                             key=lambda node: one(graph, node, KG.rowIndex).toPython())
            if len(records) != len(rows):
                raise ValueError(f"Rows lost/duplicated in {table_name}")
            if set(graph.subjects(RDF.type, KG[class_name])) != set(records):
                raise ValueError(f"Unmapped/extra {class_name} records")
            table_nulls = 0
            for index, (row, record) in enumerate(zip(rows, records)):
                check_scalar(one(graph, record, KG.rowIndex), index, XSD.integer)
                if one(graph, record, KG.snapshot) != snapshot or one(graph, record, KG.table) != table_node:
                    raise ValueError("Wrong snapshot or source table link")
                matrix_values = decoder.decode(one(graph, record, KG.matrixValues)) if table_name == "hero_feature_matrix" else None
                if matrix_values is not None and (type(matrix_values) is not list or len(matrix_values) != len(matrix_order)):
                    raise ValueError("Matrix vector length changed")
                expected_missing = set()
                for column_index, (field, column, predicate) in enumerate(zip(table.schema, columns, predicates)):
                    expected = row[field.name]
                    cells += 1
                    if expected is None:
                        nulls += 1
                        table_nulls += 1
                        expected_missing.add(column)
                    if table_name == "hero_feature_matrix" and field.name != "hero_id":
                        if not same_json(expected, matrix_values[column_index - 1]):
                            raise ValueError("Matrix value/type/float64 changed")
                    else:
                        values = list(graph.objects(record, predicate))
                        if expected is None:
                            if values:
                                raise ValueError(f"Null changed: {table_name}/{index}/{field.name}")
                        elif len(values) != 1:
                            raise ValueError("Value missing or duplicated")
                        else:
                            check_scalar(values[0], expected, arrow_datatype(field.type))
                if set(graph.objects(record, KG.missingColumn)) != expected_missing:
                    raise ValueError("Null column markers changed")
                wanted = {structured_predicate(name) for name in JSON_COLUMNS.get(table_name, set()) if row[name] is not None}
                actual_properties = {predicate for predicate in graph.predicates(record, None) if predicate in STRUCTURED_PROPERTIES}
                if actual_properties != wanted:
                    raise ValueError("Missing or extra direct structured fields")
                for name in JSON_COLUMNS.get(table_name, set()):
                    if row[name] is not None:
                        original = json.loads(row[name])
                        actual = decoder.decode(one(graph, record, structured_predicate(name)))
                        if not same_json(original, actual):
                            raise ValueError("Normalized JSON differs")
                        json_fields += 1
            rows_by_table[table_name], records_by_table[table_name] = rows, records
            report["tables"][table_name] = {"rows": len(rows), "columns": table.num_columns,
                                           "cells": len(rows) * table.num_columns, "nulls": table_nulls}
        if len(set(graph.objects(snapshot, KG.hasTable))) != len(TABLES):
            raise ValueError("Extra dataset tables")
        report.update({"cells_verified": cells, "null_cells_verified": nulls,
                       "normalized_json_fields_verified": json_fields})
        checks.append("Every Parquet cell restored with original Arrow type, float64 bits, nulls and row/column order")
        print(f"Проверены все {cells} ячеек. Проверка связей и параметров...", file=sys.stderr, flush=True)
        feature_index = {}
        hero_record_index = {row["hero_id"]: node for row, node in zip(rows_by_table["heroes"], records_by_table["heroes"])}
        meta_index = {row["hero_id"]: (node, row) for row, node in zip(rows_by_table["hero_meta"], records_by_table["hero_meta"])}
        hero_ids = set(hero_record_index)
        item_ids = {row["item_id"] for row in rows_by_table["items"]}
        valve_timestamp = json.loads((root / "data/raw/valve/manifest.json").read_text()).get("retrieved_at")
        for table_name, rows in rows_by_table.items():
            for row, record in zip(rows, records_by_table[table_name]):
                for name, kind, prop in (("hero_id", "hero", KG.hero), ("opponent_hero_id", "hero", KG.opponent),
                                         ("ability_id", "ability", KG.ability), ("item_id", "item", KG.item)):
                    if name in row:
                        if one(graph, record, prop) != entity(kind, row[name]):
                            raise ValueError(f"Wrong semantic entity link: {table_name}/{name}")
                        if kind == "hero" and row[name] not in hero_ids or kind == "item" and row[name] not in item_ids:
                            raise ValueError("Missing referenced hero/item")
                for name, prop in SEMANTIC_FIELDS.items():
                    if name in row:
                        actual = one(graph, record, prop, optional=row[name] is None)
                        if row[name] is None:
                            if actual is not None:
                                raise ValueError("Invented semantic value")
                        else:
                            check_scalar(actual, row[name], XSD.double if type(row[name]) is float else
                                         XSD.integer if type(row[name]) is int else XSD.string)
                source = row.get("source") if "source" in row else "valve" if table_name in ("heroes", "abilities", "items") else None
                if one(graph, record, KG.source, optional=source is None) != (entity("source", source) if source else None):
                    raise ValueError("Invented or incorrect data source")
                timestamp = row.get("retrieved_at") if "retrieved_at" in row else valve_timestamp if table_name in ("heroes", "abilities", "items") else None
                actual_time = one(graph, record, KG.collectedAt, optional=timestamp is None)
                if timestamp is None:
                    if actual_time is not None:
                        raise ValueError("Invented collection time")
                elif actual_time.datatype != XSD.dateTime or actual_time.toPython() != datetime.fromisoformat(timestamp):
                    raise ValueError("Collection time changed")
                provenance = {artifacts[f"data/final/{table_name}.parquet"]}
                raw_path = None
                if table_name in ("heroes", "abilities"):
                    raw_path = f"data/raw/valve/heroes/{row['hero_id']}.json"
                elif table_name == "items":
                    raw_path = f"data/raw/valve/items/{row['item_id']}.json"
                elif table_name == "hero_items":
                    raw_path = f"data/raw/opendota/itemPopularity/{row['hero_id']}.json"
                elif table_name == "matchups" and row.get("source") == "opendota":
                    raw_path = f"data/raw/opendota/matchups/{row['hero_id']}.json"
                elif table_name == "hero_meta":
                    raw_path = "data/raw/opendota/heroStats.json"
                if raw_path in artifacts:
                    provenance.add(artifacts[raw_path])
                if set(graph.objects(record, PROV.wasDerivedFrom)) != provenance:
                    raise ValueError("Observation provenance changed")
                if table_name == "hero_features":
                    if one(graph, record, KG.feature) != entity("feature", row["feature"]):
                        raise ValueError("Wrong feature")
                    check_scalar(one(graph, record, KG.method), row["derivation_method"], XSD.string)
                    feature_index[(row["hero_id"], row["feature"])] = (record, row["value"])
                    candidates = {entity("ability", a["ability_id"]) for a in rows_by_table["abilities"]
                                  if a["hero_id"] == row["hero_id"] and any(row["evidence"].startswith(
                                      "Valve ability " + a["display_name"] + suffix)
                                      for suffix in (":", " damage code =", " immunity code ="))}
                    if set(graph.objects(record, KG.supportedByAbility)) != (candidates if len(candidates) == 1 else set()):
                        raise ValueError("Ability evidence link changed")
                if table_name == "hero_items":
                    check_scalar(one(graph, record, KG.phase), row["phase"], XSD.string)
                if table_name == "matchups":
                    if row["hero_id"] == row["opponent_hero_id"]:
                        raise ValueError("Self matchup")
                    if row["wins"] is not None and not 0 <= row["wins"] <= row["matches"]:
                        raise ValueError("Invalid wins/matches")
                    baseline = one(graph, record, KG.baselineStatistics, optional=row["normalized_advantage"] is None)
                    expected_baseline = meta_index[row["hero_id"]][0] if row["normalized_advantage"] is not None else None
                    if baseline != expected_baseline:
                        raise ValueError("Wrong baseline statistics")
                    if baseline is not None and abs(row["win_rate"] - meta_index[row["hero_id"]][1]["win_rate"] - row["normalized_advantage"]) > 1e-15:
                        raise ValueError("Advantage formula differs from source definition")
        for table_name, kind, id_field in (("heroes", "hero", "hero_id"), ("abilities", "ability", "ability_id"), ("items", "item", "item_id")):
            expected_entities = {entity(kind, row[id_field]) for row in rows_by_table[table_name]}
            if set(graph.subjects(RDF.type, KG[kind.title()])) != expected_entities:
                raise ValueError("Stable entity population changed")
            for row, record in zip(rows_by_table[table_name], records_by_table[table_name]):
                node = entity(kind, row[id_field])
                if set(graph.objects(node, KG.hasRecord)) != {record}:
                    raise ValueError("Entity snapshot link changed")
                check_scalar(one(graph, node, KG.identifier), row[id_field], XSD.integer)
                if str(one(graph, node, RDFS.label)) != (row["display_name"] or row["internal_name"]):
                    raise ValueError("Entity label changed")
        feature_names = {row["feature"] for row in rows_by_table["hero_features"]}
        if set(graph.subjects(RDF.type, KG.Feature)) != {entity("feature", name) for name in feature_names}:
            raise ValueError("Feature population changed")
        units = {"ARMOR_PERCENTILE": "ratio", "BASE_MAGIC_RESISTANCE": "percent", "BASE_ARMOR": "armor",
                 "MOVEMENT_SPEED": "game_distance_per_second", "ATTACK_RANGE": "game_distance",
                 "MAX_AOE_RADIUS": "game_distance", "MAX_CONTROL_DURATION": "seconds"}
        for name in feature_names:
            feature = entity("feature", name)
            kind = "count" if name.endswith("_COUNT") else "numeric" if name in NUMERIC_FEATURES else "binary"
            check_scalar(one(graph, feature, KG.identifier), name, XSD.string)
            check_scalar(one(graph, feature, KG.valueKind), kind, XSD.string)
            check_scalar(one(graph, feature, KG.unit), units.get(name, "count" if kind == "count" else "indicator"), XSD.string)
            if str(one(graph, feature, RDFS.label)) != FEATURE_LABELS.get(name, name):
                raise ValueError("Feature label changed")
        for hid, record in hero_record_index.items():
            expected_abilities = {a for row, a in zip(rows_by_table["abilities"], records_by_table["abilities"]) if row["hero_id"] == hid}
            if set(graph.objects(record, KG.hasAbilitySnapshot)) != expected_abilities:
                raise ValueError("Hero ability navigation changed")
            expected_features = {node for (hero_id, _), (node, _) in feature_index.items() if hero_id == hid}
            if set(graph.objects(entity("hero", hid), KG.hasFeatureObservation)) != expected_features:
                raise ValueError("Hero feature navigation changed")
        matrix_count, joined_observations, zero_observations = 0, 0, 0
        for row, vector in zip(rows_by_table["hero_feature_matrix"], records_by_table["hero_feature_matrix"]):
            values = decoder.decode(one(graph, vector, KG.matrixValues))
            for index, name in enumerate(matrix_order):
                assertion = feature_index.get((row["hero_id"], name))
                if not same_json(values[index], assertion[1] if assertion else 0.0):
                    raise ValueError("Matrix and feature observations differ")
                if assertion:
                    if one(graph, assertion[0], KG.snapshot) != one(graph, vector, KG.snapshot) or one(graph, assertion[0], KG.hero) != one(graph, vector, KG.hero):
                        raise ValueError("Matrix evidence join changed")
                    joined_observations += 1
                    zero_observations += assertion[1] == 0.0
                matrix_count += 1
        rank_count = 0
        rank_nodes = set()
        for row, parent in zip(rows_by_table["hero_meta"], records_by_table["hero_meta"]):
            actual = sorted(graph.objects(parent, KG.hasRankObservation), key=lambda n: one(graph, n, KG.sourceIndex).toPython())
            expected = json.loads(row["rank_brackets"] or "[]")
            if len(actual) != len(expected):
                raise ValueError("Rank bracket count changed")
            for index, (original, node) in enumerate(zip(expected, actual)):
                if node in rank_nodes:
                    raise ValueError("Rank observation reused across source rows")
                rank_nodes.add(node)
                check_scalar(one(graph, node, KG.sourceIndex), index, XSD.integer)
                if one(graph, node, KG.hero) != entity("hero", row["hero_id"]) or one(graph, node, KG.snapshot) != snapshot or one(graph, node, PROV.wasDerivedFrom) != parent:
                    raise ValueError("Rank hero/snapshot/provenance changed")
                check_scalar(one(graph, node, KG.rankBracket), original["rank_bracket"], XSD.string)
                for name in ("matches", "wins", "win_rate"):
                    value = one(graph, node, SEMANTIC_FIELDS[name], optional=original[name] is None)
                    if original[name] is None:
                        if value is not None:
                            raise ValueError("Invented rank win rate")
                    else:
                        check_scalar(value, original[name], XSD.double if name == "win_rate" else XSD.integer)
                rank_count += 1
        if set(graph.subjects(RDF.type, KG.RankBracketObservation)) != rank_nodes:
            raise ValueError("Missing or orphan rank observations")
        parameter_count = 0
        parameter_nodes = set()
        for name in ("abilities", "items"):
            for row, parent in zip(rows_by_table[name], records_by_table[name]):
                nodes = sorted(graph.objects(parent, KG.hasParameter), key=lambda n: one(graph, n, KG.sourceIndex).toPython())
                expected = json.loads(row["special_values"] or "[]")
                if len(nodes) != len(expected):
                    raise ValueError("Parameter count changed")
                for index, (original, node) in enumerate(zip(expected, nodes)):
                    if node in parameter_nodes:
                        raise ValueError("Parameter reused across source rows")
                    parameter_nodes.add(node)
                    check_scalar(one(graph, node, KG.sourceIndex), index, XSD.integer)
                    check_scalar(one(graph, node, KG.parameterName), original.get("name", ""), XSD.string)
                    if not same_json(original, decoder.decode(one(graph, node, KG.parameterData))):
                        raise ValueError("Parameter values changed")
                    for key, prop in (("values_float", KG.baseValues), ("values_shard", KG.shardValues), ("values_scepter", KG.scepterValues)):
                        actual_series = one(graph, node, prop, optional=not isinstance(original.get(key), list))
                        if isinstance(original.get(key), list):
                            if not same_json(original[key], decoder.decode(actual_series)):
                                raise ValueError("Parameter array alias changed")
                        elif actual_series is not None:
                            raise ValueError("Invented parameter array")
                    parameter_count += 1
        if set(graph.subjects(RDF.type, KG.Parameter)) != parameter_nodes:
            raise ValueError("Missing or orphan parameters")
        report.update({"matrix_values_verified": matrix_count, "matrix_evidence_joins_verified": joined_observations,
                       "zero_valued_feature_observations_verified": zero_observations,
                       "rank_observations_verified": rank_count, "parameters_verified": parameter_count})
        checks.append("Semantic entity links, observations, matrix arrays/evidence joins, rank brackets and parameters match")
        print("Связи, матрица и параметры проверены. Проверка SHACL...", file=sys.stderr, flush=True)
        shapes = Graph().parse(output / "shapes.ttl", format="turtle")
        selected = Graph()
        selected += Graph().parse(output / "schema.ttl", format="turtle")
        for predicate in set(shapes.objects(None, SH.path)) | {RDF.type}:
            for triple in graph.triples((None, predicate, None)):
                selected.add(triple)
        conforms, _, results = validate(selected, shacl_graph=shapes, inference="none", advanced=False, inplace=True)
        report["shacl_conforms"], report["shacl_report"] = bool(conforms), results
        if not conforms:
            raise ValueError("SHACL shapes did not conform")
        checks.append("SHACL types, required links, cardinalities, sample counts and rate bounds conform")
        print("SHACL пройден. Проверка SPARQL...", file=sys.stderr, flush=True)
        del selected
        from .ontology_queries import competency_checks
        report["competency_queries"] = competency_checks(graph, root / "queries")
        checks.append("SPARQL competency queries return expected results from the source tables")
        report["class_counts"] = {
            name: sum(1 for _ in graph.subjects(RDF.type, KG[name])) for name in
            ("Hero", "Ability", "Item", "Feature", "FeatureObservation", "FeatureVector", "MatchupObservation",
             "ItemUsageObservation", "HeroStatisticsObservation", "RankBracketObservation", "Parameter", "SourceArtifact")}
        matchups = rows_by_table["matchups"]
        report["preserved_source_limitations"] = {
            "matchups_with_unknown_source_wins_advantage_time": sum(row["source"] is None for row in matchups),
            "matchups_below_50_games": sum(row["matches"] < 50 for row in matchups),
            "matrix_zero_does_not_prove_absence": True,
            "collection_timestamp_is_not_patch_date": True}
        report["ontology_sha256"] = sha((output / "dota2.ttl").read_bytes())
        package = sorted([*output.glob("*.ttl"), output / "catalog-v001.xml"])
        report["package_files"] = {path.name: {"bytes": path.stat().st_size, "sha256": sha(path.read_bytes())} for path in package}
        if any(entry["bytes"] >= 100_000_000 for entry in report["package_files"].values()):
            raise ValueError("Ontology module is too large for a normal Git file")
        report["package_sha256"] = sha(json.dumps([(name, value["sha256"]) for name, value in report["package_files"].items()], separators=(",", ":")).encode())
        report["conforms"] = True
    except Exception as error:
        errors.append(f"{type(error).__name__}: {error}")
    (output / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
