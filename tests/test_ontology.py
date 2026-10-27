from pathlib import Path
import json
import shutil
import tempfile
import unittest

from rdflib import Graph, Literal, OWL, RDF, RDFS, XSD
from pyshacl import validate

from dota_kg.ontology import literal, load_ontology, turtle_bytes, write_package
from dota_kg.ontology_schema import KG, RES, schema_graph
from dota_kg.ontology_domain import informative_parameter, checked_parameter
from dota_kg.ontology_domain_validation import DomainReader, validate_game
from dota_kg.ontology_validation import _package_structure, check_scalar, make_shapes, same_json, validate_hierarchy, validate_relation_types


class OntologyTests(unittest.TestCase):
    def test_canonical_schema_edits_survive_loading_export_and_validation(self):
        source = Path(__file__).resolve().parents[1] / "ontology"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "ontology"
            directory.mkdir()
            shutil.copyfile(source / "schema.ttl", directory / "schema.ttl")
            graph = Graph().parse(directory / "schema.ttl", format="turtle")
            graph.set((KG.Hero, RDFS.comment, Literal("Editable domain comment")))
            graph.serialize(directory / "schema.ttl", format="turtle")
            core = schema_graph(root)
            self.assertIn((KG.Hero, RDFS.comment, Literal("Editable domain comment")), core)
            output = root / "export"
            output.mkdir()
            write_package(root, {}, output)
            loaded = load_ontology(output)
            self.assertIn((KG.Hero, RDFS.comment, Literal("Editable domain comment")), loaded)
            structure = _package_structure(loaded, output)
            self.assertEqual(structure["named_classes"], len(set(core.subjects(RDF.type, OWL.Class))))
            self.assertEqual(set(structure["imports"]), {path.name for path in output.glob("*.ttl")})
            loaded.add((RES.unexpectedClass, RDF.type, OWL.Class))
            with self.assertRaisesRegex(ValueError, "canonical schema"):
                _package_structure(loaded, output)

    def test_hierarchy_keeps_homogeneous_categories_and_rejects_misplaced_classes(self):
        graph = schema_graph()
        hierarchy = validate_hierarchy(graph)
        self.assertEqual({name for name, parents in hierarchy.items() if not parents},
                         {"Entity", "GameCharacteristic", "GameFact", "Statistics"})
        graph.add((KG.Hero, RDFS.subClassOf, KG.Statistics))
        with self.assertRaises(ValueError):
            validate_hierarchy(graph)
        graph = schema_graph()
        graph.set((KG.Hero, RDFS.subClassOf, KG.Hero))
        with self.assertRaises(ValueError):
            validate_hierarchy(graph)

    def test_relation_domains_ranges_and_disjoint_classes_are_compatible(self):
        graph = schema_graph()
        item, note = RES.fixtureItem, RES.fixtureNote
        graph.add((item, RDF.type, KG.Item))
        graph.add((note, RDF.type, KG.GameNote))
        graph.add((item, KG.hasNote, note))
        self.assertGreater(validate_relation_types(graph)["relation_endpoints_checked"], 0)
        graph.set((KG.hasNote, RDFS.domain, KG.Ability))
        with self.assertRaisesRegex(ValueError, "declared domain"):
            validate_relation_types(graph)
        graph.remove((KG.hasNote, RDFS.domain, None))
        graph.add((item, RDF.type, KG.Ability))
        with self.assertRaisesRegex(ValueError, "disjoint"):
            validate_relation_types(graph)
        graph.remove((item, RDF.type, KG.Ability))
        graph.set((KG.hasNote, RDFS.range, KG.Hero))
        with self.assertRaisesRegex(ValueError, "declared range"):
            validate_relation_types(graph)

    def test_domain_values_and_modifiers_survive_without_technical_layers(self):
        root = Path(__file__).resolve().parents[1]
        parameter = {"name": "bonus_strength", "values_float": [0.0, -0.0, 1, 1],
                     "is_percentage": False, "heading_loc": "", "values_shard": [0],
                     "values_scepter": [], "required_facet": "facet_x",
                     "bonuses": [{"name": "talent_x", "value": 0, "operation": 0}],
                     "facet_bonus": {"name": "facet_x", "values": [0.25], "operation": 1}}
        cooldown = {**parameter, "name": "AbilityCooldown", "values_float": [95],
                    "values_shard": [], "required_facet": "", "bonuses": [],
                    "facet_bonus": {"name": "", "values": [], "operation": 0}}
        rows = {"heroes": [{"hero_id": 86, "display_name": "Fixture Hero", "internal_name": "fixture_hero",
                             "facets": "[]", "role_levels": "[3, 0]", "raw": "{}"}],
                "items": [{"item_id": 116, "display_name": "Fixture Item", "internal_name": "item_fixture",
                           "cost": 0, "special_values": json.dumps([parameter, cooldown]),
                           "bonuses": json.dumps([parameter]), "cooldown": "[95]", "mana_cost": "[]"}]}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            write_package(root, rows, output)
            game = load_ontology(output)
            self.assertEqual(set(p.name for p in output.glob("*.ttl")),
                             {"schema.ttl", "game.ttl", "data-entities.ttl", "data-features.ttl", "data-statistics.ttl"})
            self.assertFalse(any(str(p).endswith(("#source", "#snapshot", "#rawRecord", "#method"))
                                 for _, p, _ in game))
            parameter_node = RES["item/116/parameter/0"]
            self.assertTrue(same_json(informative_parameter(parameter), DomainReader(game).parameter(parameter_node)))
            projection = validate_game(game, rows)
            self.assertEqual(projection["informative_values"]["facet_modifiers_verified"], 1)
            self.assertEqual(projection["informative_values"]["bonus_modifiers_verified"], 1)
            self.assertEqual(len(set(game.subjects(RDF.type, KG.Parameter))), 2)
            value_node = next(game.objects(parameter_node, KG.hasBaseValue))
            game.remove((value_node, KG.numericValue, None))
            with self.assertRaises(ValueError):
                validate_game(game, rows)

    @staticmethod
    def _gameplay_fixture():
        parameter = {"name": "lift_duration", "values_float": [0.0, -0.0, 1.25],
                     "is_percentage": False, "heading_loc": "", "values_shard": [2.5],
                     "values_scepter": [], "required_facet": "",
                     "bonuses": [{"name": "special_bonus_fixture", "value": 0.0, "operation": 0}]}
        talent_parameter = {**parameter, "name": "value", "values_float": [1.5],
                            "values_shard": [], "bonuses": []}
        talent = {"id": 9001, "name": "special_bonus_fixture", "name_loc": "Fixture talent",
                  "desc_loc": "Changes the lift duration", "special_values": [talent_parameter],
                  "notes_loc": ["Talent mechanics"]}
        return {
            "heroes": [{"hero_id": 86, "display_name": "Fixture Hero", "internal_name": "fixture_hero",
                        "facets": "[]", "role_levels": "[3, 0]",
                        "raw": json.dumps({"turn_rate": .6, "talents": [talent]})}],
            "abilities": [{"ability_id": 5448, "hero_id": 86, "display_name": "Fixture Lift",
                           "internal_name": "fixture_lift", "special_values": json.dumps([parameter]),
                           "notes": json.dumps(["First ordinary note", "Second ordinary note"]),
                           "raw": json.dumps({"channel_times": [0.12345678901234566, 0.0, -0.0],
                                              "facets_loc": ["First facet", "", "Third facet"],
                                              "notes_loc": ["First ordinary note", "Second ordinary note"]})}],
            "hero_meta": [{"hero_id": 86, "matches": 100, "wins": 60, "win_rate": .6,
                           "rank_brackets": "[]", "raw": json.dumps({"pub_pick_trend": [1, 0, 0]})}],
        }

    def test_gameplay_raw_fields_talents_and_facet_notes_survive_without_archive(self):
        root = Path(__file__).resolve().parents[1]
        rows = self._gameplay_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            write_package(root, rows, directory)
            game = load_ontology(directory)
            result = validate_game(game, rows)
            self.assertGreater(result["informative_values"]["talents_verified"], 0)
            self.assertEqual(game.value(RES["hero/86"], KG.turnRate).toPython(), .6)
            talent = RES["hero/86/talent/0"]
            modifier = RES["ability/5448/parameter/0/modifier/bonus/0"]
            self.assertIn((modifier, KG.requiresTalent, talent), game)
            self.assertIn((RES["hero/86"], KG.hasTalent, talent), game)
            facets = {int(game.value(note, KG.position)): str(game.value(note, KG.noteText))
                      for note in game.objects(RES["ability/5448"], KG.hasNote)
                      if str(game.value(note, KG.noteKind)) == "facet"}
            self.assertEqual(facets, {0: "First facet", 2: "Third facet"})
            parameter = game.value(RES["ability/5448"], KG.hasChannelTime)
            self.assertTrue(same_json([0.12345678901234566, 0.0, -0.0],
                                     DomainReader(game).series(parameter, KG.hasBaseValue)))
            game.remove((modifier, KG.requiresTalent, talent))
            with self.assertRaisesRegex(ValueError, "Talent condition"):
                validate_game(game, rows)

    def test_raw_channel_value_corruption_and_unknown_game_fields_are_rejected(self):
        root = Path(__file__).resolve().parents[1]
        rows = self._gameplay_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            write_package(root, rows, directory)
            game = load_ontology(directory)
            parameter = game.value(RES["ability/5448"], KG.hasChannelTime)
            value = next(game.objects(parameter, KG.hasBaseValue))
            game.remove((value, KG.numericValue, None))
            with self.assertRaises(ValueError):
                validate_game(game, rows)
            raw = json.loads(rows["heroes"][0]["raw"])
            raw["unmapped_gameplay_value"] = 123
            rows["heroes"][0]["raw"] = json.dumps(raw)
            with self.assertRaisesRegex(ValueError, "Unmapped gameplay fields"):
                write_package(root, rows, directory)

    def test_unified_features_preserve_known_zero_unknown_zero_and_ability_explanations(self):
        from dota_kg.ontology_domain import DomainBuilder
        rows = {
            "heroes": [{"hero_id": 1, "display_name": "Fixture", "internal_name": "fixture",
                         "facets": "[]", "role_levels": "[]", "raw": "{}"}],
            "abilities": [{"hero_id": 1, "ability_id": 10, "display_name": "Move", "internal_name": "move",
                           "special_values": "[]", "notes": "[]", "raw": "{}"}],
            "hero_features": [{"hero_id": 1, "feature": "BASE_ARMOR", "value": 0.0,
                               "derivation_method": "rule", "evidence": "Valve hero.armor = 0.0"},
                              {"hero_id": 1, "feature": "FORCED_MOVEMENT", "value": 1.0,
                               "derivation_method": "rule", "evidence": "Valve ability Move: pushes the target"}],
            "hero_feature_matrix": [{"hero_id": 1, "BASE_ARMOR": 0.0, "SLOW": 0.0, "FORCED_MOVEMENT": 1.0}],
        }
        graph = schema_graph()
        for module in DomainBuilder(rows).build().values():
            graph += module
        validate_game(graph, rows)
        values = {str(graph.value(graph.value(n, KG.feature), KG.identifier)): n
                  for n in graph.subjects(RDF.type, KG.FeatureValue)}
        self.assertEqual(len(values), 3)
        self.assertTrue(all(not list(graph.objects(node, KG.position)) for node in values.values()))
        self.assertEqual(graph.value(values["BASE_ARMOR"], KG.value).toPython(), 0.0)
        self.assertTrue(graph.value(values["BASE_ARMOR"], KG.isObserved).toPython())
        self.assertEqual(graph.value(values["SLOW"], KG.value).toPython(), 0.0)
        self.assertFalse(graph.value(values["SLOW"], KG.isObserved).toPython())
        self.assertIsNone(graph.value(values["SLOW"], KG.evidence))
        self.assertEqual(graph.value(values["FORCED_MOVEMENT"], KG.supportedByAbility), RES["ability/10"])
        for code, prop, value in (("BASE_ARMOR", KG.isObserved, literal(False)),
                                  ("SLOW", KG.isObserved, literal(True)),
                                  ("BASE_ARMOR", KG.evidence, None),
                                  ("FORCED_MOVEMENT", KG.supportedByAbility, RES["ability/999"])):
            with self.subTest(code=code, prop=prop):
                original = list(graph.objects(values[code], prop))
                graph.remove((values[code], prop, None))
                if value is not None:
                    graph.add((values[code], prop, value))
                with self.assertRaises(ValueError):
                    validate_game(graph, rows)
                graph.remove((values[code], prop, None))
                for previous in original:
                    graph.add((values[code], prop, previous))
        rows["hero_feature_matrix"][0]["BASE_ARMOR"] = 0.5
        with self.assertRaisesRegex(ValueError, "matrix cell"):
            DomainBuilder(rows).build()

    def test_role_scores_are_hero_properties_with_explicit_zero_and_unknown_values(self):
        from dota_kg.ontology_domain import DomainBuilder
        rows = {"heroes": [{"hero_id": 1, "display_name": "Fixture", "internal_name": "fixture",
                            "facets": "[]", "role_levels": "[3, 0, 1, 2, 0, 1, 3, 0, 2]", "raw": "{}"}]}
        hero = RES["hero/1"]
        graph = schema_graph()
        for module in DomainBuilder(rows).build().values():
            graph += module
        result = validate_game(graph, rows)
        self.assertEqual(result["informative_values"]["role_levels_verified"], 9)
        expected = {KG.carryLevel: 3, KG.supportLevel: 0, KG.nukerLevel: 1, KG.disablerLevel: 2,
                    KG.junglerLevel: 0, KG.durableLevel: 1, KG.escapeLevel: 3,
                    KG.pusherLevel: 0, KG.initiatorLevel: 2}
        for prop, score in expected.items():
            self.assertEqual(graph.value(hero, prop).datatype, XSD.integer)
            self.assertEqual(graph.value(hero, prop).toPython(), score)
        graph.remove((hero, KG.supportLevel, None))
        with self.assertRaises(ValueError):
            validate_game(graph, rows)
        graph.add((hero, KG.supportLevel, literal(1)))
        with self.assertRaises(ValueError):
            validate_game(graph, rows)
        for invalid in ([0] * 10, [4], [False]):
            with self.subTest(role_levels=invalid):
                rows["heroes"][0]["role_levels"] = json.dumps(invalid)
                with self.assertRaises(ValueError):
                    DomainBuilder(rows).build()
        rows["heroes"][0]["role_levels"] = "[3]"
        partial = schema_graph()
        for module in DomainBuilder(rows).build().values():
            partial += module
        validate_game(partial, rows)
        self.assertIsNone(partial.value(hero, KG.supportLevel))

    def test_rank_detail_stays_outside_rdf_and_aggregate_statistics_remain(self):
        from dota_kg.ontology_domain import DomainBuilder
        from rdflib.compare import isomorphic
        rows = {"heroes": [{"hero_id": 1, "display_name": "Fixture", "internal_name": "fixture",
                            "facets": "[]", "role_levels": "[]", "raw": "{}"}],
                "hero_meta": [{"hero_id": 1, "matches": 100, "wins": 60, "win_rate": .6,
                               "rank_brackets": json.dumps([{"rank_bracket": "8", "matches": 10,
                                                             "wins": 9, "win_rate": .9}]), "raw": "{}"}]}
        graph = schema_graph()
        for module in DomainBuilder(rows).build().values():
            graph += module
        validate_game(graph, rows)
        statistics = RES["record/hero_meta/1"]
        self.assertEqual(graph.value(statistics, KG.matches).toPython(), 100)
        self.assertEqual(graph.value(statistics, KG.wins).toPython(), 60)
        self.assertEqual(graph.value(statistics, KG.winRate).toPython(), .6)
        rows["hero_meta"][0]["rank_brackets"] = "[]"
        alternate = schema_graph()
        for module in DomainBuilder(rows).build().values():
            alternate += module
        self.assertTrue(isomorphic(graph, alternate))

    def test_ordered_series_preserves_duplicates_zero_and_float64_precision(self):
        graph = Graph()
        expected = [1, 1, 0, 0.0, -0.0, 0.12345678901234566, 1e-20]
        for index, value in enumerate(expected):
            node = RES[f"testParameter/value/{index}"]
            graph.add((RES.testParameter, KG.hasBaseValue, node))
            graph.add((node, KG.position, literal(index)))
            graph.add((node, KG.numericValue, literal(value)))
        saved = Graph().parse(data=turtle_bytes(graph), format="turtle")
        self.assertTrue(same_json(expected, DomainReader(saved).series(RES.testParameter, KG.hasBaseValue)))
        saved.remove((RES["testParameter/value/1"], KG.position, None))
        with self.assertRaises(ValueError):
            DomainReader(saved).series(RES.testParameter, KG.hasBaseValue)

    def test_typed_scalar_check_does_not_confuse_false_zero_or_float_rounding(self):
        check_scalar(literal(False), False, XSD.boolean)
        with self.assertRaises(ValueError):
            check_scalar(literal(0), False, XSD.boolean)
        with self.assertRaises(ValueError):
            check_scalar(literal(0.1235), 0.123456789, XSD.double)
        with self.assertRaises(ValueError):
            check_scalar(literal(-0.0), 0.0, XSD.double)

    def test_unmapped_parameter_fields_and_false_operation_are_not_discarded(self):
        parameter = {"name": "duration", "values_float": [1], "is_percentage": False,
                     "heading_loc": "", "bonuses": [], "values_shard": [],
                     "values_scepter": [], "required_facet": ""}
        with self.assertRaisesRegex(ValueError, "Unmapped parameter"):
            checked_parameter({**parameter, "new_gameplay_field": 123})
        changed = {**parameter, "facet_bonus": {"name": "", "values": [], "operation": False}}
        self.assertIn("facet_bonus", informative_parameter(changed))

    def test_shapes_accept_optional_unknown_wins_and_reject_invalid_win_rate(self):
        graph = schema_graph()
        hero, opponent, observation = RES.testHero, RES.testOpponent, RES.testMatchup
        for node, identifier in ((hero, 1), (opponent, 2)):
            graph.add((node, RDF.type, KG.Hero))
            graph.add((node, KG.identifier, literal(identifier)))
        for triple in [
            (observation, RDF.type, KG.MatchupObservation),
            (observation, KG.hero, hero), (observation, KG.opponent, opponent),
            (observation, KG.matches, literal(10)),
            (observation, KG.winRate, literal(.6)),
        ]:
            graph.add(triple)
        self.assertTrue(validate(graph, shacl_graph=make_shapes(), inference="none")[0])
        graph.set((observation, KG.winRate, literal(1.2)))
        self.assertFalse(validate(graph, shacl_graph=make_shapes(), inference="none")[0])


if __name__ == "__main__":
    unittest.main()
