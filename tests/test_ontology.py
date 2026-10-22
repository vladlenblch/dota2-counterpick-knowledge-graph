from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

from rdflib import Graph, Literal, OWL, RDF, RDFS, XSD
from pyshacl import validate

from dota_kg.ontology import JsonTree, literal, load_ontology, turtle_bytes, write_package
from dota_kg.ontology_schema import KG, RES, PROV, schema_graph
from dota_kg.ontology_validation import JsonDecoder, _package_structure, check_scalar, make_shapes, same_json


class OntologyTests(unittest.TestCase):
    def test_json_roundtrip_preserves_order_duplicates_null_false_and_signed_zero(self):
        original = {"array": [1, 1, None, False, "", [], {}, 0.0, -0.0],
                    "nested": {"description": "first\nsecond", "empty": [],
                               "precision": [3.6000001, 0.12345678901234566, 1e-20]}}
        graph = Graph()
        root = JsonTree(graph).encode(original)
        graph.add((RES.test, KG.jsonRoot, root))
        saved = turtle_bytes(graph)
        parsed = Graph().parse(data=saved, format="turtle")
        restored_root = next(parsed.objects(RES.test, KG.jsonRoot))
        self.assertTrue(same_json(original, JsonDecoder(parsed).decode(restored_root)))

    def test_json_decoder_detects_missing_array_entry(self):
        graph = Graph()
        root = JsonTree(graph).encode([10, 20])
        entry = next(graph.objects(root, KG.hasEntry))
        graph.remove((root, KG.hasEntry, entry))
        with self.assertRaises(ValueError):
            JsonDecoder(graph).decode(root)

    def test_json_kind_distinguishes_null_from_empty_containers_and_scalars(self):
        for original, kind in ((None, "null"), ([], "array"), ({}, "object"),
                               (False, "scalar"), (0, "scalar"), ("", "scalar")):
            with self.subTest(original=original, kind=kind):
                graph = Graph()
                root = JsonTree(graph).encode(original)
                self.assertIn((root, RDF.type, KG.JsonValue), graph)
                self.assertEqual(list(graph.objects(root, KG.jsonKind)), [literal(kind)])
                self.assertTrue(same_json(original, JsonDecoder(graph).decode(root)))

    def test_json_decoder_rejects_conflicting_kind(self):
        graph = Graph()
        root = JsonTree(graph).encode([])
        graph.add((root, KG.jsonKind, literal("object")))
        with self.assertRaises(ValueError):
            JsonDecoder(graph).decode(root)

    def test_canonical_schema_edits_survive_loading_export_and_validation(self):
        source = Path(__file__).resolve().parents[1] / "ontology"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "ontology"
            directory.mkdir()
            for filename in ("schema.ttl", "archive-schema.ttl"):
                shutil.copyfile(source / filename, directory / filename)
            graph = Graph().parse(directory / "schema.ttl", format="turtle")
            graph.add((RES.schemaEdit, RDF.type, OWL.Class))
            graph.add((RES.schemaEdit, RDFS.label, Literal("An editable canonical class")))
            graph.serialize(directory / "schema.ttl", format="turtle")
            core = schema_graph(root)
            self.assertIn((RES.schemaEdit, RDF.type, OWL.Class), core)
            self.assertNotIn((KG.DatasetTable, RDF.type, OWL.Class), core)
            technical = schema_graph(root, include_technical=True)
            self.assertIn((KG.DatasetTable, RDF.type, OWL.Class), technical)
            output = root / "export"
            output.mkdir()
            exporter = SimpleNamespace(root=root, schema=technical, g=technical,
                                       artifacts={}, snapshot_id="editable-schema-test")
            write_package(exporter, output)
            loaded = load_ontology(output)
            self.assertIn((RES.schemaEdit, RDF.type, OWL.Class), loaded)
            self.assertNotIn((KG.DatasetTable, RDF.type, OWL.Class),
                             load_ontology(output, include_archive=False))
            structure = _package_structure(loaded, output)
            self.assertEqual(structure["named_classes"], {
                "main": len(set(core.subjects(RDF.type, OWL.Class))),
                "full": len(set(technical.subjects(RDF.type, OWL.Class))),
            })
            self.assertEqual(set(structure["imports"]["full"]),
                             {path.name for path in output.glob("*.ttl")})
            loaded.add((RES.unexpectedClass, RDF.type, OWL.Class))
            with self.assertRaisesRegex(ValueError, "canonical schemas"):
                _package_structure(loaded, output)

    def test_typed_scalar_check_does_not_confuse_false_zero_or_float_rounding(self):
        check_scalar(literal(False), False, XSD.boolean)
        with self.assertRaises(ValueError):
            check_scalar(literal(0), False, XSD.boolean)
        with self.assertRaises(ValueError):
            check_scalar(literal(0.1235), 0.123456789, XSD.double)
        with self.assertRaises(ValueError):
            check_scalar(literal(-0.0), 0.0, XSD.double)

    def test_feature_query_compares_numeric_values_across_double_lexical_forms(self):
        query_directory = Path(__file__).resolve().parents[1] / "queries"
        movement_query = (query_directory / "03_forced_movement.rq").read_text()
        for lexical in ("1.0e0", "1.0"):
            with self.subTest(lexical=lexical):
                graph = Graph()
                for identifier, name, value, evidence in (
                        (86, "Rubick", lexical, "Telekinesis moves the target"),
                        (1, "Other hero", "0.0", "No displacement")):
                    hero = RES[f"hero/{identifier}"]
                    observation = RES[f"testObservation/{identifier}/FORCED_MOVEMENT"]
                    for triple in (
                            (hero, KG.identifier, literal(identifier)),
                            (hero, RDFS.label, literal(name)),
                            (observation, RDF.type, KG.FeatureObservation),
                            (observation, KG.hero, hero),
                            (observation, KG.feature, RES["feature/FORCED_MOVEMENT"]),
                            (observation, KG.value, Literal(value, datatype=XSD.double, normalize=False)),
                            (observation, KG.evidence, literal(evidence))):
                        graph.add(triple)
                movement = [tuple(value.toPython() for value in row)
                            for row in graph.query(movement_query)]
                self.assertEqual(movement, [(86, "Rubick", "Telekinesis moves the target")])
                self.assertEqual(str(graph.value(RES["testObservation/86/FORCED_MOVEMENT"], KG.value)), lexical)

    def test_item_query_accepts_plain_and_typed_string_phases(self):
        query = (Path(__file__).resolve().parents[1]
                 / "queries/04_rubick_late_items.rq").read_text()
        for phase in (Literal("late"), Literal("late", datatype=XSD.string)):
            with self.subTest(datatype=phase.datatype):
                graph = Graph()
                item = RES["item/1"]
                graph.add((item, KG.identifier, literal(1)))
                graph.add((item, RDFS.label, literal("Blink Dagger")))
                for identifier, observed_phase, uses in (
                        ("late", phase, 42), ("early", Literal("early", datatype=XSD.string), 999)):
                    observation = RES["testItemUsage/" + identifier]
                    for triple in (
                            (observation, RDF.type, KG.ItemUsageObservation),
                            (observation, KG.hero, RES["hero/86"]),
                            (observation, KG.item, item),
                            (observation, KG.phase, observed_phase),
                            (observation, KG.usageCount, literal(uses))):
                        graph.add(triple)
                rows = [tuple(value.toPython() for value in row) for row in graph.query(query)]
                self.assertEqual(rows, [(1, "Blink Dagger", 42)])

    def test_shapes_accept_optional_unknown_wins_and_reject_invalid_win_rate(self):
        graph = schema_graph()
        hero, opponent, observation = RES.testHero, RES.testOpponent, RES.testMatchup
        for node, identifier in ((hero, 1), (opponent, 2)):
            graph.add((node, RDF.type, KG.Hero))
            graph.add((node, KG.identifier, literal(identifier)))
        graph.add((RES.testSnapshot, RDF.type, KG.DatasetSnapshot))
        graph.add((RES.testArtifact, RDF.type, KG.SourceArtifact))
        for triple in [
            (observation, RDF.type, KG.MatchupObservation),
            (observation, KG.hero, hero), (observation, KG.opponent, opponent),
            (observation, KG.snapshot, RES.testSnapshot),
            (observation, PROV.wasDerivedFrom, RES.testArtifact),
            (observation, KG.matches, literal(10)),
            (observation, KG.winRate, literal(.6)),
        ]:
            graph.add(triple)
        self.assertTrue(validate(graph, shacl_graph=make_shapes(), inference="none")[0])
        graph.set((observation, KG.winRate, literal(1.2)))
        self.assertFalse(validate(graph, shacl_graph=make_shapes(), inference="none")[0])


if __name__ == "__main__":
    unittest.main()
