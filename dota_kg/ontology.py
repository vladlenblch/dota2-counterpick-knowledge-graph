"""Export the game datasets into a locally bundled OWL/RDF ontology."""
from __future__ import annotations

import gc
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import quote
import xml.etree.ElementTree as ET

from rdflib import BNode, Graph, Literal, RDF, RDFS, OWL, XSD, URIRef
from rdflib.plugins.serializers.turtle import TurtleSerializer

from .common import ROOT
from .ontology_schema import RES, TABLES, TABLE_PATHS, bind, schema_graph


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
    import pyarrow.parquet as pq

    paths = sorted((Path(root) / "data/final").glob("*.parquet"))
    expected = {name for name, path in TABLE_PATHS.items() if path.parent == Path("data/final")}
    if {p.stem for p in paths} != expected:
        raise ValueError("Input tables differ from the explicit ontology mapping")
    rows = {name: pq.read_table(Path(root) / path).to_pylist() for name, path in TABLE_PATHS.items()}
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


def _read_graph_cache(source):
    payload = json.load(source)
    terms = []
    for kind, value, datatype, language in payload["terms"]:
        if kind == "uri":
            term = URIRef(value)
        elif kind == "blank":
            term = BNode(value)
        elif kind == "literal":
            term = Literal(value, datatype=URIRef(datatype) if datatype else None,
                           lang=language, normalize=False)
        else:
            raise ValueError("Unknown cached RDF term")
        terms.append(term)
    graph = bind(Graph())
    graph.addN((terms[s], terms[p], terms[o], graph) for s, p, o in payload["triples"])
    return graph


def _write_graph_cache(graph, stream):
    indexes, terms, triples = {}, [], []
    for triple in graph:
        row = []
        for term in triple:
            if term not in indexes:
                indexes[term] = len(terms)
                if isinstance(term, Literal):
                    terms.append(["literal", str(term), str(term.datatype) if term.datatype else None, term.language])
                else:
                    terms.append(["blank" if isinstance(term, BNode) else "uri", str(term), None, None])
            row.append(indexes[term])
        triples.append(row)
    json.dump({"terms": terms, "triples": triples}, stream, ensure_ascii=False, separators=(",", ":"))


def load_ontology(directory: Path, *, progress=None, cache=False) -> Graph:
    """Resolve all imports from the local catalog without network requests."""
    directory = Path(directory).resolve()
    ns = {"c": "urn:oasis:names:tc:entity:xmlns:xml:catalog"}
    catalog_path = directory / "catalog-v001.xml"
    catalog = ET.parse(catalog_path)
    mapping = {e.attrib["name"]: e.attrib["uri"] for e in catalog.findall("c:uri", ns)}
    fingerprint = None
    cache_path = directory / ".cache" / "game.json"
    if cache:
        sources = {(directory / name).resolve() for name in (*mapping.values(), "game.ttl")}
        digest = hashlib.sha256(catalog_path.read_bytes())
        for path in sorted(sources):
            if not path.is_relative_to(directory) or not path.is_file():
                raise ValueError("Import must be a bundled local ontology file")
            digest.update(str(path.relative_to(directory)).encode("utf-8"))
            digest.update(path.read_bytes())
        fingerprint = "# source-sha256: " + digest.hexdigest() + "\n"
        try:
            with cache_path.open(encoding="utf-8") as source:
                if source.readline() == fingerprint:
                    if progress:
                        progress("Чтение кэша графа...")
                    return _read_graph_cache(source)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            if progress:
                progress("Кэш недоступен или повреждён; читаю исходный RDF...")
    graph, seen, pending = bind(Graph()), set(), [directory / "game.ttl"]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError("Import must be a bundled local ontology file")
        seen.add(path)
        if progress:
            progress(f"Чтение {path.name}...")
        graph.parse(path, format="turtle")
        for identifier in graph.objects(None, OWL.imports):
            if str(identifier) not in mapping:
                raise ValueError(f"Unmapped ontology import: {identifier}")
            pending.append((directory / mapping[str(identifier)]).resolve())
    if cache:
        temporary = None
        try:
            if progress:
                progress("Сохранение кэша для следующих запусков...")
            cache_path.parent.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_path.parent,
                                             suffix=".json", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(fingerprint)
                _write_graph_cache(graph, stream)
            os.replace(temporary, cache_path)
        except OSError:
            if progress:
                progress("Не удалось сохранить кэш; граф загружен из RDF.")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
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
