"""Export the game datasets into a locally bundled OWL/RDF ontology."""
from __future__ import annotations

import gc
import hashlib
import io
import math
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import quote
import xml.etree.ElementTree as ET

import pyarrow.parquet as pq
from rdflib import Graph, Literal, RDF, RDFS, OWL, XSD, URIRef
from rdflib.plugins.serializers.turtle import TurtleSerializer

from .common import ROOT
from .ontology_schema import RES, TABLES, bind, schema_graph


class ExactTurtleSerializer(TurtleSerializer):
    """Keep full float precision instead of the abbreviated Turtle shorthand."""
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
    if datatype is None:
        if isinstance(value, bool): datatype = XSD.boolean
        elif isinstance(value, int): datatype = XSD.integer
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Nonfinite game value cannot be silently changed")
            datatype = XSD.double
        elif isinstance(value, str): datatype = XSD.string
        else: raise TypeError(f"Unsupported scalar: {type(value)}")
    return Literal(value, datatype=datatype, normalize=False)


def record_uri(table: str, row: dict) -> URIRef:
    key = "_".join(quote(str(row[name]), safe="") for name in TABLES[table][1])
    return RES[f"record/{table}/{key}"]


def entity(kind: str, identifier) -> URIRef:
    return RES[f"{kind}/{quote(str(identifier), safe='')}"]


def read_tables(root: Path) -> dict:
    paths = sorted((Path(root) / "data/final").glob("*.parquet"))
    if {p.stem for p in paths} != set(TABLES):
        raise ValueError("Input tables differ from the explicit ontology mapping")
    rows = {p.stem: pq.read_table(p).to_pylist() for p in paths}
    for table, records in rows.items():
        keys = [tuple(r[k] for k in TABLES[table][1]) for r in records]
        if len(set(keys)) != len(keys):
            raise ValueError(f"Duplicate natural key in {table}")
    return rows


def write_package(root: Path, rows: dict, output: Path):
    """Copy the editable schema and write only named game entities and facts."""
    from .ontology_domain import DomainBuilder

    output.mkdir(parents=True, exist_ok=True)
    source = Path(root) / "ontology/schema.ttl"
    schema = schema_graph(root)
    identifiers = list(schema.subjects(RDF.type, OWL.Ontology))
    if len(identifiers) != 1:
        raise ValueError("Expected one ontology declaration in schema.ttl")
    if source.resolve() != (output / "schema.ttl").resolve():
        shutil.copy2(source, output / "schema.ttl")
    modules = {"schema.ttl": identifiers[0]}

    def save(filename, identifier, graph, label, imports):
        graph.add((identifier, RDF.type, OWL.Ontology))
        graph.add((identifier, RDFS.label, Literal(label, lang="ru")))
        for imported in imports:
            graph.add((identifier, OWL.imports, imported))
        serialize_turtle(graph, output / filename)
        modules[filename] = identifier

    labels = {"entities": "Герои, способности, предметы и параметры",
              "features": "Признаки героев и их значения",
              "statistics": "Матчапы, использование предметов и статистика героев"}
    for name, graph in DomainBuilder(rows).build().items():
        save(f"data-{name}.ttl", RES[f"graph/data-{name}"], graph, labels[name], [identifiers[0]])
    save("game.ttl", RES["graph/game"], bind(Graph()), "Dota 2: онтология и игровые данные", list(modules.values()))
    ns = "urn:oasis:names:tc:entity:xmlns:xml:catalog"
    ET.register_namespace("", ns)
    catalog = ET.Element(f"{{{ns}}}catalog", prefer="public")
    for filename, identifier in sorted(modules.items()):
        ET.SubElement(catalog, f"{{{ns}}}uri", name=str(identifier), uri=filename)
    ET.indent(catalog)
    ET.ElementTree(catalog).write(output / "catalog-v001.xml", encoding="utf-8", xml_declaration=True)


def load_ontology(directory: Path) -> Graph:
    """Resolve all imports from the local catalog without network requests."""
    directory = Path(directory).resolve()
    ns = {"c": "urn:oasis:names:tc:entity:xmlns:xml:catalog"}
    catalog = ET.parse(directory / "catalog-v001.xml")
    mapping = {e.attrib["name"]: e.attrib["uri"] for e in catalog.findall("c:uri", ns)}
    graph, seen, pending = bind(Graph()), set(), [directory / "game.ttl"]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError("Import must be a bundled local ontology file")
        seen.add(path)
        module = Graph().parse(path, format="turtle")
        graph += module
        for identifier in module.objects(None, OWL.imports):
            if str(identifier) not in mapping:
                raise ValueError(f"Unmapped ontology import: {identifier}")
            pending.append((directory / mapping[str(identifier)]).resolve())
    return graph


def export_ontology(root: Path = ROOT, output: Path | None = None):
    from .ontology_validation import make_shapes, validate_export

    root, output = Path(root), Path(output or Path(root) / "ontology")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ontology-", dir=output.parent) as temporary:
        staging = Path(temporary)
        write_package(root, read_tables(root), staging)
        serialize_turtle(make_shapes(), staging / "shapes.ttl")
        gc.collect()
        report = validate_export(root, staging)
        if not report["conforms"]:
            raise RuntimeError(f"Ontology validation failed: {report['errors']}")
        output.mkdir(parents=True, exist_ok=True)
        for path in staging.iterdir():
            os.replace(path, output / path.name)
        return report
