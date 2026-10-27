"""Check the domain RDF, its hierarchy, SHACL constraints and source table values."""
from __future__ import annotations

import json
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pyarrow.parquet as pq
from rdflib import BNode, Graph, Literal, RDF, RDFS, OWL, XSD, URIRef
from pyshacl import validate

from .ontology_schema import KG, SH, TABLES, ROLE_PROPERTIES, bind, schema_graph


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
    rules = {cls: [] for cls, _ in TABLES.values()}
    for cls in ("Hero", "Ability", "Item"):
        rules[cls] = [prop(KG.identifier, XSD.integer)]
    rules["Hero"] += [prop(p, XSD.integer, lo=0, lower=0, upper=3) for p in ROLE_PROPERTIES]
    rules["Ability"] += [prop(KG.hero, KG.Hero)]
    rules["Feature"] = [prop(KG.identifier, XSD.string), prop(KG.valueKind, XSD.string), prop(KG.unit, XSD.string)]
    rules["MatchupObservation"] += [
        prop(KG.hero, KG.Hero), prop(KG.opponent, KG.Hero),
        prop(KG.matches, XSD.integer, lower=1), prop(KG.wins, XSD.integer, lo=0, lower=0),
        prop(KG.winRate, XSD.double, lower=0, upper=1),
        prop(KG.normalizedAdvantage, XSD.double, lo=0, lower=-1, upper=1)]
    rules["Hero"] += [prop(KG.hasFeatureValue, KG.FeatureValue, lo=0, hi=None)]
    rules["FeatureValue"] = [prop(KG.hero, KG.Hero), prop(KG.feature, KG.Feature),
                             prop(KG.value, XSD.double), prop(KG.isObserved, XSD.boolean),
                             prop(KG.evidence, XSD.string, lo=0),
                             prop(KG.supportedByAbility, KG.Ability, lo=0)]
    rules["ItemUsageObservation"] += [prop(KG.hero, KG.Hero), prop(KG.item, KG.Item),
        prop(KG.phase, XSD.string), prop(KG.usageCount, XSD.integer, lower=0)]
    rules["HeroStatisticsObservation"] += [prop(KG.hero, KG.Hero),
        prop(KG.matches, XSD.integer, lower=0), prop(KG.wins, XSD.integer, lower=0),
        prop(KG.winRate, XSD.double, lo=0, lower=0, upper=1)]
    rules["Parameter"] = [prop(KG.parameterName, XSD.string), prop(KG.isPercentage, XSD.boolean, lo=0),
        prop(KG.hasBaseValue, KG.ParameterValue, lo=0, hi=None),
        prop(KG.hasShardValue, KG.ParameterValue, lo=0, hi=None),
        prop(KG.hasScepterValue, KG.ParameterValue, lo=0, hi=None),
        prop(KG.hasModifier, KG.ParameterModifier, lo=0, hi=None)]
    rules["ParameterValue"] = [prop(KG.position, XSD.integer, lower=0), prop(KG.numericValue, None)]
    rules["ParameterModifier"] = [prop(KG.modifierKind, XSD.string), prop(KG.conditionName, XSD.string),
        prop(KG.operation, XSD.integer), prop(KG.position, XSD.integer, lower=0),
        prop(KG.numericValue, None, lo=0), prop(KG.hasBaseValue, KG.ParameterValue, lo=0, hi=None)]
    rules["GameNote"] = [prop(KG.noteText, XSD.string), prop(KG.noteKind, XSD.string),
                         prop(KG.position, XSD.integer, lower=0)]
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
            else:
                graph.add((constraint, SH.nodeKind, SH.Literal))
            if lower is not None:
                graph.add((constraint, SH.minInclusive, Literal(lower)))
            if upper is not None:
                graph.add((constraint, SH.maxInclusive, Literal(upper)))
    return graph


def _package_structure(graph, output):
    """Verify a single domain package and its canonical editable schema."""
    output = Path(output).resolve()
    ns = {"c": "urn:oasis:names:tc:entity:xmlns:xml:catalog"}
    entries = ET.parse(output / "catalog-v001.xml").findall("c:uri", ns)
    mapping = {entry.attrib["name"]: entry.attrib["uri"] for entry in entries}
    if len(mapping) != len(entries) or len(set(mapping.values())) != len(mapping):
        raise ValueError("Duplicate ontology catalog mapping")
    expected = {"game.ttl", "schema.ttl", "data-entities.ttl", "data-features.ttl", "data-statistics.ttl"}
    if set(mapping.values()) != expected or {p.name for p in output.glob("*.ttl")} - expected - {"shapes.ttl"}:
        raise ValueError("The ontology package must contain only domain modules")
    for filename in mapping.values():
        path = (output / filename).resolve()
        if not path.is_relative_to(output) or not path.is_file():
            raise ValueError("Ontology imports must resolve to existing bundled files")
    main = Graph().parse(output / "game.ttl", format="turtle")
    roots = list(main.subjects(RDF.type, OWL.Ontology))
    if len(roots) != 1:
        raise ValueError("Expected one domain ontology entrypoint")
    reached, pending = set(), [roots[0]]
    while pending:
        node = pending.pop()
        if node in reached:
            continue
        reached.add(node)
        if (node, RDF.type, OWL.Ontology) not in graph:
            raise ValueError(f"Missing imported ontology: {node}")
        for imported in graph.objects(node, OWL.imports):
            if str(imported) not in mapping:
                raise ValueError(f"Unmapped ontology import: {imported}")
            pending.append(imported)
    files = {mapping.get(str(node)) for node in reached}
    if files != expected:
        raise ValueError(f"Unexpected domain import closure: {files}")
    core = Graph().parse(output / "schema.ttl", format="turtle")
    for kind in (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty):
        declared = {n for n in core.subjects(RDF.type, kind) if isinstance(n, URIRef)}
        actual = {n for n in graph.subjects(RDF.type, kind) if isinstance(n, URIRef)}
        if declared != actual:
            raise ValueError("Loaded declarations differ from the canonical schema")
    # Schema axioms belong to the editable schema, never generated data modules.
    schema_predicates = {RDFS.subClassOf, RDFS.domain, RDFS.range, OWL.disjointWith}
    for predicate in schema_predicates:
        if set(graph.triples((None, predicate, None))) != set(core.triples((None, predicate, None))):
            raise ValueError("Generated data changed canonical schema axioms")
    return {"imports": sorted(files),
            "named_classes": len(set(core.subjects(RDF.type, OWL.Class)))}


def validate_hierarchy(graph):
    """Enforce homogeneous sibling groups: entities, characteristics, facts, statistics."""
    groups = {
        "Entity": {"Hero", "Ability", "Item"},
        "GameCharacteristic": {"Feature", "Parameter"},
        "GameFact": {"FeatureValue", "ParameterValue", "ParameterModifier", "GameNote"},
        "Statistics": {"HeroStatisticsObservation", "MatchupObservation", "ItemUsageObservation"},
    }
    expected = {KG[root]: set() for root in groups}
    for root, children in groups.items():
        expected.update({KG[child]: {KG[root]} for child in children})
    classes = {n for n in graph.subjects(RDF.type, OWL.Class) if isinstance(n, URIRef)}
    # Talent is a gameplay specialization of an ability when represented.
    if KG.Talent in classes:
        expected[KG.Talent] = {KG.Ability}
    if classes != set(expected):
        raise ValueError("Domain classes differ from the explicit homogeneous hierarchy")
    for cls, parents in expected.items():
        actual = set(graph.objects(cls, RDFS.subClassOf)) - {OWL.Thing}
        if actual != parents:
            raise ValueError(f"Class placed at an incompatible hierarchy level: {cls}")
    return {str(node).rsplit("#", 1)[-1]:
            [str(parent).rsplit("#", 1)[-1] for parent in parents]
            for node, parents in sorted(expected.items(), key=lambda item: str(item[0]))}


def validate_relation_types(graph):
    """Check declared relation endpoints and disjoint classes using subclass closure."""
    classes = set(graph.subjects(RDF.type, OWL.Class))
    ancestors = {cls: ({cls} | set(graph.transitive_objects(cls, RDFS.subClassOf))) for cls in classes}
    memberships = {}

    def types(node):
        if node not in memberships:
            memberships[node] = set().union(*(ancestors[cls] for cls in graph.objects(node, RDF.type) if cls in ancestors))
        return memberships[node]

    checked = 0
    for prop in set(graph.subjects(RDF.type, OWL.ObjectProperty)) | set(graph.subjects(RDF.type, OWL.DatatypeProperty)):
        domains = set(graph.objects(prop, RDFS.domain)) & classes
        ranges = set(graph.objects(prop, RDFS.range)) & classes
        if not domains and not ranges:
            continue
        for subject, _, target in graph.triples((None, prop, None)):
            if not domains <= types(subject):
                raise ValueError(f"Relation subject violates its declared domain: {subject}/{prop}")
            if not ranges <= types(target):
                raise ValueError(f"Relation object violates its declared range: {prop}/{target}")
            checked += 1
    disjoint = []
    for declaration in graph.subjects(RDF.type, OWL.AllDisjointClasses):
        for members in graph.objects(declaration, OWL.members):
            disjoint.append(set(graph.items(members)))
    disjoint += [{left, right} for left, _, right in graph.triples((None, OWL.disjointWith, None))]
    for cls in classes:
        for node in graph.subjects(RDF.type, cls):
            if any(len(types(node) & group) > 1 for group in disjoint):
                raise ValueError(f"An individual belongs to disjoint domain classes: {node}")
    return {"relation_endpoints_checked": checked, "disjoint_groups_checked": len(disjoint)}


def validate_export(root: Path, output: Path):
    from .ontology import load_ontology, read_tables, sha
    from .ontology_domain_validation import validate_game
    root, output = Path(root), Path(output)
    checks, errors = [], []
    report = {"conforms": False, "ontology": "ontology/game.ttl", "checks": checks, "errors": errors}
    try:
        print("Чтение предметного RDF-графа...", file=sys.stderr, flush=True)
        graph = load_ontology(output)
        report["triples"] = len(graph)
        report["package_structure"] = _package_structure(graph, output)
        canonical = schema_graph(root)
        saved_schema = Graph().parse(output / "schema.ttl", format="turtle")
        from rdflib.compare import isomorphic
        if not isomorphic(canonical, saved_schema):
            raise ValueError("Exported schema differs from the editable canonical schema")
        report["hierarchy"] = validate_hierarchy(saved_schema)
        checks.append("На каждом уровне иерархии однородные классы: игровые сущности, характеристики, факты и статистика")
        rows = read_tables(root)
        report["tables"] = {}
        cells = 0
        for name in TABLES:
            table = pq.read_table(root / f"data/final/{name}.parquet")
            report["tables"][name] = {"rows": table.num_rows, "columns": table.num_columns}
            cells += table.num_rows * table.num_columns
        report["source_tables"] = len(rows)
        report["cells_checked"] = cells
        print("Проверка игровых значений, параметров и связей...", file=sys.stderr, flush=True)
        report["game_model"] = validate_game(graph, rows)
        report["relation_types"] = validate_relation_types(graph)
        report["class_counts"] = report["game_model"]["class_counts"]
        checks.append("Все игровые значения, связи, параметры, порядок, модификаторы и основания признаков сверены с таблицами")
        print("Проверка ограничений SHACL...", file=sys.stderr, flush=True)
        shapes = Graph().parse(output / "shapes.ttl", format="turtle")
        if not isomorphic(shapes, make_shapes()):
            raise ValueError("Saved SHACL constraints differ from the canonical rules")
        selected = Graph()
        selected += saved_schema
        for predicate in set(shapes.objects(None, SH.path)) | {RDF.type}:
            for triple in graph.triples((None, predicate, None)):
                selected.add(triple)
        conforms, _, details = validate(selected, shacl_graph=shapes, inference="none", advanced=False, inplace=True)
        report["shacl_conforms"], report["shacl_report"] = bool(conforms), details
        if not conforms:
            raise ValueError("SHACL constraints did not conform")
        checks.append("SHACL: типы, обязательные связи, мощности, количества и границы долей корректны")
        matchups = rows["matchups"]
        report["data_limitations"] = {
            "matchups_with_unknown_exact_wins": sum(r["wins"] is None for r in matchups),
            "matchups_with_unknown_advantage": sum(r["normalized_advantage"] is None for r in matchups),
            "matrix_zero_does_not_prove_absence": True,
        }
        files = sorted([*output.glob("*.ttl"), output / "catalog-v001.xml"])
        report["package_files"] = {p.name: {"bytes": p.stat().st_size, "sha256": sha(p.read_bytes())} for p in files}
        report["package_sha256"] = sha(json.dumps([(name, value["sha256"]) for name, value in report["package_files"].items()], separators=(",", ":")).encode())
        report["conforms"] = True
    except Exception as error:
        errors.append(f"{type(error).__name__}: {error}")
    (output / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
