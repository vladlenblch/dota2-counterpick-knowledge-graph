"""Lossless Parquet/JSON/CSV -> OWL/RDF export, with independently decoded validation.

Run: python -m dota_kg ontology
The ontology/dota2.ttl entry point opens its bundled local imports in Protege.
Source tables are observations tied to a content-addressed snapshot; source files
are retained byte-for-byte in the graph and JSON has an ordered queryable tree.
"""
from __future__ import annotations

import base64
import csv
import gc
import hashlib
import io
import json
import math
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import pyarrow as pa
import pyarrow.parquet as pq
from rdflib import BNode, Graph, Literal, RDF, RDFS, OWL, XSD, URIRef
from rdflib.plugins.serializers.turtle import TurtleSerializer

from .common import ROOT
from .ontology_schema import (
    KG, RES, PROV, TABLES, JSON_COLUMNS, FEATURE_LABELS, NUMERIC_FEATURES,
    bind, schema_graph, column_predicate, structured_predicate, SEMANTIC_FIELDS,
    JSON_TEXT_PROPERTIES, STRUCTURED_PROPERTIES, DATA_GROUPS, SCHEMA_FILES,
)

KEYS = {table: keys for table, (_, keys) in TABLES.items()}

class ExactTurtleSerializer(TurtleSerializer):
    """Default RDFLib shorthand rounds doubles to ~7 digits; keep their full lexical value."""
    def label(self, node, position):
        if isinstance(node, Literal) and node.datatype in (XSD.double, XSD.float, XSD.decimal):
            return node.n3()
        return super().label(node, position)

def turtle_bytes(graph: Graph) -> bytes:
    stream = io.BytesIO()
    ExactTurtleSerializer(graph).serialize(stream, encoding="utf-8")
    return stream.getvalue()

def serialize_turtle(graph: Graph, path: Path):
    with path.open("wb") as stream:
        ExactTurtleSerializer(graph).serialize(stream, encoding="utf-8")

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def literal(value, datatype=None) -> Literal:
    """Preserve int/bool distinctions, float64 precision, whitespace and empty strings."""
    if datatype is None:
        if isinstance(value, bool): datatype = XSD.boolean
        elif isinstance(value, int): datatype = XSD.integer
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Nonfinite source float cannot be silently changed")
            datatype = XSD.double
        elif isinstance(value, str): datatype = XSD.string
        else: raise TypeError(f"Unsupported scalar: {type(value)}")
    return Literal(value, datatype=datatype, normalize=False)

def column_uri(snapshot: str, table: str, name: str) -> URIRef:
    return RES[f"column/{snapshot}/{table}/{quote(name, safe='')}"]

def record_uri(snapshot: str, table: str, row: dict) -> URIRef:
    key = "_".join(quote(str(row[name]), safe="") for name in KEYS[table])
    return RES[f"record/{snapshot}/{table}/{key}"]

def entity(kind: str, identifier) -> URIRef:
    return RES[f"{kind}/{quote(str(identifier), safe='')}"]

def arrow_datatype(dtype):
    if pa.types.is_boolean(dtype): return XSD.boolean
    if pa.types.is_integer(dtype): return XSD.integer
    if pa.types.is_floating(dtype): return XSD.double
    if pa.types.is_string(dtype): return XSD.string
    raise TypeError(f"Unmapped Arrow datatype: {dtype}")

class JsonTree:
    """Content-shared JSON containers; array entries always retain their index."""
    def __init__(self, graph: Graph):
        self.graph = graph
        self.seen = set()

    def encode(self, value):
        if value is None:
            node = RES["json/null"]
            self.graph.add((node, RDF.type, KG.JsonValue))
            self.graph.add((node, KG.jsonKind, literal("null")))
            return node
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                               allow_nan=False).encode("utf-8")
        digest = sha(canonical)
        node = BNode("j" + digest)
        if digest in self.seen:
            return node
        self.seen.add(digest)
        self.graph.add((node, RDF.type, KG.JsonValue))
        if not isinstance(value, (dict, list)):
            self.graph.add((node, KG.jsonKind, literal("scalar")))
            self.graph.add((node, KG.jsonScalar, literal(value)))
            return node
        self.graph.add((node, KG.jsonKind, literal("object" if isinstance(value, dict) else "array")))
        self.graph.add((node, KG.entryCount, literal(len(value))))
        entries = sorted(value.items()) if isinstance(value, dict) else enumerate(value)
        for key, child in entries:
            entry = BNode("e" + sha((digest + "\0" + str(key)).encode("utf-8")))
            self.graph.add((node, KG.hasEntry, entry))
            # hasEntry has range JsonEntry; explicit per-entry typing would duplicate 300k triples.
            self.graph.add((entry, KG.entryKey if isinstance(value, dict) else KG.entryIndex, literal(key)))
            if child is None or isinstance(child, (dict, list)):
                self.graph.add((entry, KG.jsonChild, self.encode(child)))
            else:
                self.graph.add((entry, KG.jsonScalar, literal(child)))
        return node

class Exporter:
    def __init__(self, root: Path):
        self.root = root
        self.paths = sorted(p for p in (root / "data").rglob("*") if p.is_file()
                            and not any(part.startswith(".") for part in p.relative_to(root).parts))
        self.inventory = [(p.relative_to(root).as_posix(), sha(p.read_bytes())) for p in self.paths]
        if not self.inventory:
            raise ValueError("No data files found")
        self.snapshot_id = sha(json.dumps(self.inventory, separators=(",", ":")).encode())
        self.snapshot = RES[f"snapshot/{self.snapshot_id}"]
        # The editable Turtle schemas are the only source of schema declarations.
        self.schema = schema_graph(root, include_technical=True)
        self.g = bind(Graph())
        self.g += self.schema
        for table in TABLES:
            self.g.bind(table + "Record", RES[f"record/{self.snapshot_id}/{table}/"])
            self.g.bind(table + "Column", RES[f"column/{self.snapshot_id}/{table}/"])
        for kind in ("hero", "ability", "item", "feature", "source"):
            self.g.bind(kind, RES[kind + "/"])
        self.json = JsonTree(self.g)
        self.artifacts = {}
        self.rows = {}
        self.records = {}
        self.g.add((self.snapshot, RDF.type, KG.DatasetSnapshot))
        self.g.add((self.snapshot, KG.sha256, literal(self.snapshot_id)))
        self.g.add((self.snapshot, RDFS.label, Literal("Полный снимок исходных данных Dota 2", lang="ru")))

    def add_artifacts(self):
        for path, (relative, digest) in zip(self.paths, self.inventory):
            data = path.read_bytes()
            artifact = RES["artifact/" + sha((relative + "\0" + digest).encode())]
            self.artifacts[relative] = artifact
            self.g.add((artifact, RDF.type, KG.SourceArtifact))
            self.g.add((artifact, KG.relativePath, literal(relative)))
            self.g.add((artifact, KG.sha256, literal(digest)))
            self.g.add((artifact, KG.byteLength, literal(len(data))))
            self.g.add((self.snapshot, KG.hasArtifact, artifact))
            suffix = path.suffix.lower()
            media = {".json": "application/json", ".csv": "text/csv",
                     ".parquet": "application/vnd.apache.parquet"}.get(suffix, "application/octet-stream")
            self.g.add((artifact, KG.mediaType, literal(media)))
            if suffix in (".json", ".csv"):
                text = data.decode("utf-8")
                self.g.add((artifact, KG.rawText, literal(text)))
                value = json.loads(text) if suffix == ".json" else list(csv.DictReader(io.StringIO(text)))
                self.g.add((artifact, KG.jsonRoot, self.json.encode(value)))
            else:
                self.g.add((artifact, KG.binaryContent,
                            literal(base64.b64encode(data).decode("ascii"))))
                self.g.add((artifact, KG.contentEncoding, literal("base64")))
            parts = path.relative_to(self.root).parts
            if len(parts) > 2 and parts[1] == "raw":
                source = entity("source", parts[2])
                self.g.add((source, RDF.type, KG.DataSource))
                self.g.add((source, RDFS.label, literal(parts[2])))
                self.g.add((artifact, KG.source, source))

    def add_tables(self):
        parquet_paths = sorted((self.root / "data" / "final").glob("*.parquet"))
        if {path.stem for path in parquet_paths} != set(TABLES):
            raise ValueError("Input Parquet tables differ from the explicit ontology mapping")
        for path in parquet_paths:
            name = path.stem
            table = pq.read_table(path)
            rows = table.to_pylist()
            table_uri = RES[f"table/{self.snapshot_id}/{name}"]
            self.rows[name] = rows
            self.records[name] = []
            artifact = self.artifacts[path.relative_to(self.root).as_posix()]
            self.g.add((table_uri, RDF.type, KG.DatasetTable))
            self.g.add((table_uri, RDFS.label, literal(name)))
            self.g.add((table_uri, KG.rowCount, literal(table.num_rows)))
            self.g.add((table_uri, KG.columnCount, literal(table.num_columns)))
            self.g.add((table_uri, PROV.wasDerivedFrom, artifact))
            self.g.add((self.snapshot, KG.hasTable, table_uri))
            if name == "hero_feature_matrix":
                names = [field.name for field in table.schema if field.name != "hero_id"]
                self.g.add((self.snapshot, KG.matrixFeatureOrder, self.json.encode(names)))
            for index, field in enumerate(table.schema):
                column = column_uri(self.snapshot_id, name, field.name)
                prop = column_predicate(name, field.name)
                expected_type = (OWL.ObjectProperty if name == "hero_feature_matrix" and field.name != "hero_id"
                                 else OWL.DatatypeProperty)
                if (prop, RDF.type, expected_type) not in self.schema:
                    raise ValueError(f"Source field has no canonical schema declaration: {name}.{field.name}")
                self.g.add((column, RDF.type, KG.SourceColumn))
                self.g.add((column, KG.columnName, literal(field.name)))
                self.g.add((column, KG.columnIndex, literal(index)))
                self.g.add((column, KG.arrowType, literal(str(field.type))))
                self.g.add((column, KG.nullable, literal(field.nullable)))
                self.g.add((column, KG.columnPredicate, prop))
                self.g.add((table_uri, KG.hasColumn, column))
                if name == "hero_feature_matrix" and field.name != "hero_id":
                    self.g.add((column, KG.valueIndex, literal(index - 1)))
            seen = set()
            for index, row in enumerate(rows):
                record = record_uri(self.snapshot_id, name, row)
                if record in seen:
                    raise ValueError(f"Duplicate natural key in {name}: {row}")
                seen.add(record)
                self.records[name].append(record)
                self.g.add((record, RDF.type, KG[TABLES[name][0]]))
                self.g.add((record, KG.snapshot, self.snapshot))
                self.g.add((record, KG.table, table_uri))
                self.g.add((record, KG.rowIndex, literal(index)))
                self.g.add((record, PROV.wasDerivedFrom, artifact))
                self.g.add((table_uri, KG.hasRow, record))
                if name == "hero_feature_matrix":
                    values = [row[field.name] for field in table.schema if field.name != "hero_id"]
                    self.g.add((record, KG.matrixValues, self.json.encode(values)))
                for field in table.schema:
                    value = row[field.name]
                    if value is None:
                        self.g.add((record, KG.missingColumn, column_uri(self.snapshot_id, name, field.name)))
                    elif name != "hero_feature_matrix" or field.name == "hero_id":
                        self.g.add((record, column_predicate(name, field.name),
                                    literal(value, arrow_datatype(field.type))))
                    if value is not None and field.name in JSON_COLUMNS.get(name, set()):
                        parsed = json.loads(value)
                        self.g.add((record, structured_predicate(field.name), self.json.encode(parsed)))

    def add_entities(self):
        for table, kind, id_field, cls in (("heroes", "hero", "hero_id", KG.Hero),
                                          ("abilities", "ability", "ability_id", KG.Ability),
                                          ("items", "item", "item_id", KG.Item)):
            for row, record in zip(self.rows[table], self.records[table]):
                node = entity(kind, row[id_field])
                self.g.add((node, RDF.type, cls))
                self.g.add((node, KG.identifier, literal(row[id_field])))
                self.g.add((node, RDFS.label, literal(row["display_name"] or row["internal_name"])))
                self.g.add((node, KG.hasRecord, record))
                self.g.add((record, KG[kind], node))
                self.g.add((record, KG.source, entity("source", "valve")))
                manifest = self.root / "data/raw/valve/manifest.json"
                timestamp = json.loads(manifest.read_text()).get("retrieved_at")
                if timestamp:
                    self.g.add((record, KG.collectedAt, literal(timestamp, XSD.dateTime)))
                # Explicit source file link supplements the exact normalized-table provenance.
                path = (f"data/raw/valve/heroes/{row['hero_id']}.json" if table != "items"
                        else f"data/raw/valve/items/{row['item_id']}.json")
                if path in self.artifacts:
                    self.g.add((record, PROV.wasDerivedFrom, self.artifacts[path]))
        hero_records = {row["hero_id"]: record for row, record
                        in zip(self.rows["heroes"], self.records["heroes"])}
        item_ids = {row["item_id"] for row in self.rows["items"]}
        feature_names = sorted({row["feature"] for row in self.rows["hero_features"]})
        for name in feature_names:
            feature = entity("feature", name)
            kind = "count" if name.endswith("_COUNT") else "numeric" if name in NUMERIC_FEATURES else "binary"
            self.g.add((feature, RDF.type, KG.Feature))
            self.g.add((feature, KG.valueKind, literal(kind)))
            self.g.add((feature, KG.identifier, literal(name)))
            self.g.add((feature, RDFS.label, Literal(FEATURE_LABELS.get(name, name), lang="ru")))
            units = {"ARMOR_PERCENTILE": "ratio", "BASE_MAGIC_RESISTANCE": "percent",
                     "BASE_ARMOR": "armor", "MOVEMENT_SPEED": "game_distance_per_second",
                     "ATTACK_RANGE": "game_distance", "MAX_AOE_RADIUS": "game_distance",
                     "MAX_CONTROL_DURATION": "seconds"}
            self.g.add((feature, KG.unit, literal(units.get(name, "count" if kind == "count" else "indicator"))))
        meta_records = {row["hero_id"]: record for row, record
                        in zip(self.rows["hero_meta"], self.records["hero_meta"])}
        for table, rows in self.rows.items():
            for row, record in zip(rows, self.records[table]):
                if "hero_id" in row:
                    if row["hero_id"] not in hero_records:
                        raise ValueError(f"Unknown hero in {table}")
                    self.g.add((record, KG.hero, entity("hero", row["hero_id"])))
                if table == "abilities":
                    self.g.add((hero_records[row["hero_id"]], KG.hasAbilitySnapshot, record))
                if "opponent_hero_id" in row:
                    if row["opponent_hero_id"] not in hero_records:
                        raise ValueError("Unknown opponent")
                    self.g.add((record, KG.opponent, entity("hero", row["opponent_hero_id"])))
                if "item_id" in row:
                    if row["item_id"] not in item_ids: raise ValueError("Unknown item")
                    self.g.add((record, KG.item, entity("item", row["item_id"])))
                if row.get("source") is not None:
                    source = entity("source", row["source"])
                    self.g.add((source, RDF.type, KG.DataSource))
                    self.g.add((source, RDFS.label, literal(row["source"])))
                    self.g.add((record, KG.source, source))
                if row.get("retrieved_at") is not None:
                    self.g.add((record, KG.collectedAt, literal(row["retrieved_at"], XSD.dateTime)))
                if table == "hero_features":
                    self.g.add((record, KG.feature, entity("feature", row["feature"])))
                    self.g.add((entity("hero", row["hero_id"]), KG.hasFeatureObservation, record))
                    matches = [a for a in self.rows["abilities"] if a["hero_id"] == row["hero_id"]
                               and any(row["evidence"].startswith(f"Valve ability {a['display_name']}{suffix}")
                                       for suffix in (":", " damage code =", " immunity code ="))]
                    if len(matches) == 1:
                        self.g.add((record, KG.supportedByAbility, entity("ability", matches[0]["ability_id"])))
                elif table == "hero_items":
                    path = f"data/raw/opendota/itemPopularity/{row['hero_id']}.json"
                    if path in self.artifacts:
                        self.g.add((record, PROV.wasDerivedFrom, self.artifacts[path]))
                elif table == "matchups":
                    if row["normalized_advantage"] is not None:
                        self.g.add((record, KG.baselineStatistics, meta_records[row["hero_id"]]))
                    path = f"data/raw/opendota/matchups/{row['hero_id']}.json"
                    if row.get("source") == "opendota" and path in self.artifacts:
                        self.g.add((record, PROV.wasDerivedFrom, self.artifacts[path]))
                elif table == "hero_meta":
                    self.g.add((record, PROV.wasDerivedFrom, self.artifacts["data/raw/opendota/heroStats.json"]))
    def add_nested_observations(self):
        for row, parent in zip(self.rows["hero_meta"], self.records["hero_meta"]):
            for index, bracket in enumerate(json.loads(row["rank_brackets"] or "[]")):
                node = URIRef(str(parent) + "/rank/" + str(index))
                self.g.add((node, RDF.type, KG.RankBracketObservation))
                self.g.add((node, KG.hero, entity("hero", row["hero_id"])))
                self.g.add((node, KG.snapshot, self.snapshot))
                self.g.add((node, KG.sourceIndex, literal(index)))
                self.g.add((node, KG.rankBracket, literal(bracket["rank_bracket"])))
                self.g.add((parent, KG.hasRankObservation, node))
                self.g.add((node, PROV.wasDerivedFrom, parent))
                for key in ("matches", "wins", "win_rate"):
                    if bracket[key] is not None:
                        self.g.add((node, SEMANTIC_FIELDS[key], literal(bracket[key])))
        for table in ("abilities", "items"):
            for row, parent in zip(self.rows[table], self.records[table]):
                for index, parameter in enumerate(json.loads(row["special_values"] or "[]")):
                    node = URIRef(str(parent) + "/parameter/" + str(index))
                    self.g.add((node, RDF.type, KG.Parameter))
                    self.g.add((node, KG.parameterName, literal(parameter.get("name", ""))))
                    self.g.add((node, KG.sourceIndex, literal(index)))
                    self.g.add((node, KG.parameterData, self.json.encode(parameter)))
                    self.g.add((parent, KG.hasParameter, node))
                    for key, prop in (("values_float", KG.baseValues), ("values_shard", KG.shardValues),
                                      ("values_scepter", KG.scepterValues)):
                        if isinstance(parameter.get(key), list):
                            self.g.add((node, prop, self.json.encode(parameter[key])))

    def run(self):
        self.add_artifacts()
        self.add_tables()
        self.add_entities()
        self.add_nested_observations()
        return self

def json_closure(graph: Graph, roots) -> Graph:
    result = bind(Graph())
    pending = list(roots)
    seen = set()
    while pending:
        node = pending.pop()
        if node in seen: continue
        seen.add(node)
        for triple in graph.triples((node, None, None)):
            result.add(triple)
            if triple[1] in (KG.hasEntry, KG.jsonChild):
                pending.append(triple[2])
    return result

def write_package(exporter: Exporter, output: Path):
    """Keep the domain view small and bundle the complete source archive separately.

    Each document owns a closed JSON tree because blank nodes are document-local.
    Stable entities, observations and source file IRIs are shared across modules.
    """
    graph = exporter.g
    modules = {}
    for filename in SCHEMA_FILES:
        source = exporter.root / "ontology" / filename
        shutil.copy2(source, output / filename)
        schema = Graph().parse(source, format="turtle")
        identifiers = list(schema.subjects(RDF.type, OWL.Ontology))
        if len(identifiers) != 1:
            raise ValueError(f"Expected one ontology declaration in {filename}")
        modules[filename] = identifiers[0]
    schema_triples = set(exporter.schema)
    artifacts = set(exporter.artifacts.values())
    datasets = {name: bind(Graph()) for name in ("entities", "features", "statistics")}
    roots = {name: [] for name in datasets}
    technical = bind(Graph())
    record_prefixes = {name: str(RES[f"record/{exporter.snapshot_id}/{name}/"]) for name in TABLES}
    technical_prefixes = tuple(str(RES[f"{kind}/{exporter.snapshot_id}/"]) for kind in ("table", "column"))
    technical_predicates = {KG.table, KG.rowIndex, KG.missingColumn, KG.hasTable} | JSON_TEXT_PROPERTIES
    structured_links = STRUCTURED_PROPERTIES | {
        KG.parameterData, KG.baseValues, KG.shardValues, KG.scepterValues,
        KG.matrixValues, KG.matrixFeatureOrder,
    }
    for subject, predicate, object_ in graph:
        if isinstance(subject, BNode) or subject == RES["json/null"] or (subject, predicate, object_) in schema_triples:
            continue
        if predicate in (KG.rawText, KG.binaryContent, KG.contentEncoding) or (subject in artifacts and predicate == KG.jsonRoot):
            continue
        if str(subject).startswith(technical_prefixes) or predicate in technical_predicates:
            technical.add((subject, predicate, object_))
            continue
        table = next((name for name, prefix in record_prefixes.items() if str(subject).startswith(prefix)), None)
        group = DATA_GROUPS[table] if table else "features" if str(subject).startswith(str(RES["feature/"])) else "entities"
        datasets[group].add((subject, predicate, object_))
        if predicate in structured_links:
            roots[group].append(object_)

    def save_module(filename, identifier, module, label, imports):
        for prefix, namespace in graph.namespaces():
            module.bind(prefix, namespace)
        module.add((identifier, RDF.type, OWL.Ontology))
        module.add((identifier, RDFS.label, Literal(label, lang="ru")))
        for imported in imports:
            module.add((identifier, OWL.imports, imported))
        serialize_turtle(module, output / filename)
        modules[filename] = identifier

    for name in datasets:
        module = datasets[name]
        module += json_closure(graph, roots[name])
        identifier = RES[f"graph/{exporter.snapshot_id}/data-{name}"]
        save_module(f"data-{name}.ttl", identifier, module,
                    {"entities": "Герои, способности, предметы и параметры",
                     "features": "Признаки героев и матрица обучения",
                     "statistics": "Матчапы, предметы и статистика героев"}[name], [KG[""]])
    del datasets
    gc.collect()

    for group in ("valve", "other"):
        archive = technical if group == "other" else bind(Graph())
        archive_roots = []
        for path, artifact in exporter.artifacts.items():
            is_valve = path.startswith("data/raw/valve/")
            if is_valve != (group == "valve"):
                continue
            for triple in graph.triples((artifact, None, None)):
                archive.add(triple)
                if triple[1] == KG.jsonRoot:
                    archive_roots.append(triple[2])
        archive += json_closure(graph, archive_roots)
        identifier = RES[f"graph/{exporter.snapshot_id}/archive-{group}"]
        save_module(f"archive-{group}.ttl", identifier, archive,
                    "Исходные файлы Valve" if group == "valve" else "Остальные исходники и точная схема таблиц",
                    [modules["archive-schema.ttl"]])
        del archive
        gc.collect()

    game_id = RES[f"graph/{exporter.snapshot_id}/game"]
    game = bind(Graph())
    game.add((game_id, RDFS.comment, Literal(
        "Предметная онтология для просмотра героев, признаков, параметров и статистики. "
        "Полный исходный архив подключается через dota2.ttl.", lang="ru")))
    save_module("game.ttl", game_id, game, "Dota 2: предметная онтология и игровые данные",
                [KG[""], *(modules[f"data-{name}.ttl"] for name in ("entities", "features", "statistics"))])
    full_id = RES[f"graph/{exporter.snapshot_id}"]
    full = bind(Graph())
    full.add((full_id, RDFS.comment, Literal(
        "Полная онтология с исходными файлами и их структурой. "
        "Для просмотра игровой модели используйте game.ttl. "
        "Все локальные импорты разрешаются через catalog-v001.xml.", lang="ru")))
    save_module("dota2.ttl", full_id, full, "Dota 2: все данные и полный архив",
                [game_id, modules["archive-schema.ttl"], modules["archive-valve.ttl"], modules["archive-other.ttl"]])
    catalog_ns = "urn:oasis:names:tc:entity:xmlns:xml:catalog"
    ET.register_namespace("", catalog_ns)
    catalog = ET.Element("{" + catalog_ns + "}catalog", prefer="public")
    for filename, identifier in sorted(modules.items()):
        ET.SubElement(catalog, "{" + catalog_ns + "}uri", name=str(identifier), uri=filename)
    ET.indent(catalog)
    ET.ElementTree(catalog).write(output / "catalog-v001.xml", encoding="utf-8", xml_declaration=True)


def load_ontology(directory: Path, *, include_archive: bool = True) -> Graph:
    """Load the game or full view using only local catalog mappings."""
    directory = Path(directory).resolve()
    ns = {"c": "urn:oasis:names:tc:entity:xmlns:xml:catalog"}
    catalog = ET.parse(directory / "catalog-v001.xml")
    mapping = {entry.attrib["name"]: entry.attrib["uri"] for entry in catalog.findall("c:uri", ns)}
    graph = bind(Graph())
    pending = [directory / ("dota2.ttl" if include_archive else "game.ttl")]
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError("Import must be a bundled local ontology file")
        module = Graph().parse(path, format="turtle")
        graph += module
        for identifier in module.objects(None, OWL.imports):
            if str(identifier) not in mapping:
                raise ValueError(f"Unmapped ontology import: {identifier}")
            pending.append((directory / mapping[str(identifier)]).resolve())
    return graph


def export_ontology(root: Path = ROOT, output: Path | None = None):
    root = Path(root)
    output = Path(output or root / "ontology")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".ontology-", dir=output.parent))
    preserve_staging = False
    try:
        exporter = Exporter(root).run()
        write_package(exporter, staging)
        from .ontology_validation import make_shapes, validate_export
        serialize_turtle(make_shapes(), staging / "shapes.ttl")
        del exporter
        gc.collect()
        report = validate_export(root, staging)
        if not report["conforms"]:
            preserve_staging = True
            failure = output.parent / "ontology-export-failure.json"
            shutil.copy2(staging / "validation.json", failure)
            raise RuntimeError(f"Ontology validation failed; inspect {failure}; staged files: {staging}")
        output.mkdir(parents=True, exist_ok=True)
        previous_files = set()
        previous_report = output / "validation.json"
        if previous_report.exists():
            previous_files.update(json.loads(previous_report.read_text()).get("package_files", {}))
        current_files = {path.name for path in staging.iterdir()}
        for path in staging.iterdir():
            os.replace(path, output / path.name)
        for name in previous_files - current_files:
            if Path(name).name == name:
                (output / name).unlink(missing_ok=True)
        (output.parent / "ontology-export-failure.json").unlink(missing_ok=True)
        return report
    finally:
        if not preserve_staging:
            shutil.rmtree(staging, ignore_errors=True)
