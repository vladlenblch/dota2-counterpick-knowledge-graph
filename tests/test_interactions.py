"""Semantic regressions for conditional effect and recommendation data."""
import unittest
from pathlib import Path

from pyshacl import validate
from rdflib import Graph, RDF, RDFS

from dota_kg.ontology import literal
from dota_kg.ontology_interactions import build_interactions, validate_interactions
from dota_kg.ontology_schema import KG, RES, schema_graph
from dota_kg.ontology_validation import make_shapes
from dota_kg.answers import KnowledgeBase
from dota_kg.questions import Question


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.graph = schema_graph()

    def item(self, identifier, name, text):
        owner = RES[f"item/{identifier}"]
        for prop, value in ((RDF.type, KG.Item), (KG.internalName, literal(name)),
                            (RDFS.label, literal(name)), (KG.description, literal(text))):
            self.graph.add((owner, prop, value))
        return owner

    def feature(self, code, *, observed=True, value=1.0):
        owner = RES["ability/1"]
        feature = RES[f"feature/{code}"]
        observation = RES[f"observation/{code}"]
        for subject, prop, target in (
            (owner, RDF.type, KG.Ability), (owner, RDFS.label, literal("Test ability")),
            (feature, KG.identifier, literal(code)), (observation, RDF.type, KG.FeatureValue),
            (observation, KG.feature, feature), (observation, KG.isObserved, literal(observed)),
            (observation, KG.value, literal(value)), (observation, KG.evidence, literal("Test evidence")),
            (observation, KG.supportedByAbility, owner)):
            self.graph.add((subject, prop, target))
        return owner

    def test_unobserved_zero_and_unsupported_features_do_not_create_effects(self):
        for observed, value in ((False, 1.0), (True, 0.0), (True, 1.0)):
            with self.subTest(observed=observed, value=value):
                self.graph = schema_graph()
                owner = self.feature("STUN", observed=observed, value=value)
                enriched = build_interactions(self.graph)
                self.assertEqual(bool(list(enriched.objects(owner, KG.hasEffect))), observed and value > 0)
        self.graph.remove((None, KG.supportedByAbility, None))
        self.assertFalse(list(build_interactions(self.graph).objects(owner, KG.hasEffect)))

    def test_detection_does_not_match_mentions_of_immunity_to_true_sight(self):
        smoke = self.item(1, "item_smoke_of_deceit", "Invisibility immune to True Sight.")
        gem = self.item(2, "item_gem", "True Sight reveals invisible units.")
        enriched = build_interactions(self.graph)
        self.assertFalse(list(enriched.objects(smoke, KG.hasEffect)))
        effect = enriched.value(gem, KG.hasEffect)
        self.assertEqual(enriched.value(effect, KG.effectMechanic), RES["mechanic/DETECTION"])

    def test_changed_reviewed_item_description_requires_review(self):
        self.item(1, "item_gem", "A different mechanic after a data update.")
        with self.assertRaisesRegex(ValueError, "needs review"):
            build_interactions(self.graph)

    def test_granted_ability_requires_upgrade_item(self):
        owner = self.feature("STUN")
        item = self.item(2, "item_ultimate_scepter", "Upgrade")
        self.graph.add((owner, KG.grantedByScepter, literal(True)))
        enriched = build_interactions(self.graph)
        effect = enriched.value(owner, KG.hasEffect)
        requirement = enriched.value(effect, KG.requiresCondition)
        self.assertEqual(enriched.value(requirement, KG.conditionItem), item)
        self.assertEqual(enriched.value(requirement, KG.conditionScope), literal("source"))

    def test_explanation_query_retains_qualifications_and_target_equipment(self):
        owner = self.feature("STUN")
        bkb = self.item(2, "item_black_king_bar", "any negative effect from enemy spells has no effect")
        self.graph += build_interactions(self.graph)
        context = RES["context/opponent"]
        self.graph.add((context, KG.heldItem, bkb))
        rows = list(self.graph.query("""
            PREFIX kg: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/ontology#>
            SELECT ?source ?status ?qualification ?item WHERE {
              ?source kg:hasEffect/kg:effectMechanic ?mechanic .
              ?rule kg:sourceMechanic ?mechanic ; kg:targetMechanic ?target ;
                    kg:ruleStatus ?status ; kg:requiresCondition/kg:conditionText ?qualification ;
                    kg:blockedByCondition/kg:conditionMechanic ?blocker .
              ?context kg:heldItem ?item .
              ?item kg:hasEffect/kg:effectMechanic ?blocker .
            }
        """))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, owner)
        self.assertEqual(str(rows[0].status), "heuristic")
        self.assertEqual(rows[0].item, bkb)
        self.assertTrue(str(rows[0].qualification))

    def test_validation_rejects_invented_effects_or_lost_evidence(self):
        self.feature("STUN")
        self.graph += build_interactions(self.graph)
        validate_interactions(self.graph)
        self.graph.remove((RES["ability/1/effect/STUN"], KG.evidence, None))
        with self.assertRaisesRegex(ValueError, "Interaction model"):
            validate_interactions(self.graph)

    def test_condition_requires_structured_item_and_known_scope(self):
        node = RES["condition/test"]
        for prop, value in ((RDF.type, KG.InteractionCondition),
                            (KG.conditionKind, literal("has-item")),
                            (KG.conditionScope, literal("target")),
                            (KG.conditionText, literal("Requires an item"))):
            self.graph.add((node, prop, value))
        self.assertFalse(validate(self.graph, shacl_graph=make_shapes())[0])

        item = self.item(2, "item_test", "")
        self.graph.add((item, KG.identifier, literal(2)))
        self.graph.add((node, KG.conditionItem, item))
        self.assertTrue(validate(self.graph, shacl_graph=make_shapes())[0])
        self.graph.set((node, KG.conditionScope, literal("unknown")))
        self.assertFalse(validate(self.graph, shacl_graph=make_shapes())[0])

    def test_official_dispel_codes_produce_concrete_requirements(self):
        owner = self.feature("STUN")
        self.graph.add((owner, KG.internalName, literal("lion_impale")))
        for code, expected in ((1, "Strong"), (2, "Basic"), (3, "None")):
            with self.subTest(code=code):
                self.graph.set((owner, KG.dispellableCode, literal(code)))
                enriched = build_interactions(self.graph)
                effect = enriched.value(owner, KG.hasEffect)
                self.assertEqual(enriched.value(effect, KG.requiredDispel), literal(expected))

    def test_scythe_preferences_do_not_forbid_carries_and_bfury_requires_melee(self):
        bfury = self.item(145, "item_bfury", "Cleave (Melee Only)")
        scythe = self.item(96, "item_sheepstick", "Turns a unit harmless. Instantly destroys illusions.")
        enriched = build_interactions(self.graph)
        scythe_roles = {str(enriched.value(p, KG.roleName)): int(enriched.value(p, KG.preferenceRank))
                        for p in enriched.subjects(KG.item, scythe)}
        self.assertEqual(scythe_roles, {"support": 3, "core": 2, "carry": 1})
        preference = next(enriched.subjects(KG.item, bfury))
        self.assertEqual(enriched.value(preference, KG.attackType), literal("Melee"))
        requirement = enriched.value(enriched.value(bfury, KG.hasEffect), KG.requiresCondition)
        self.assertEqual(enriched.value(requirement, KG.conditionMechanic), RES["mechanic/MELEE"])

    def test_purchase_query_excludes_cleave_for_ranged_carry(self):
        item = self.item(145, "item_bfury", "Cleave (Melee Only)")
        self.graph.add((item, KG.isPurchasable, literal(True)))
        hero, opponent, ability, effect = (RES["hero/1"], RES["hero/12"], RES["ability/2"], RES["test/illusions"])
        for s,p,o in ((hero,RDFS.label,literal("Fixture carry")), (hero,KG.carryLevel,literal(3)),
                      (hero,KG.supportLevel,literal(0)), (opponent,RDFS.label,literal("Illusion hero")),
                      (opponent,KG.hasAbility,ability), (ability,KG.hasEffect,effect),
                      (effect,KG.effectMechanic,RES["mechanic/CREATES_ILLUSIONS"])):
            self.graph.add((s,p,o))
        self.graph += build_interactions(self.graph)
        question = Question(9, hero=hero, enemy=opponent, role="carry", phase="mid")
        self.graph.set((hero, KG.attackType, literal("Ranged")))
        self.assertEqual(KnowledgeBase(self.graph).cq09(question), [])
        self.graph.set((hero, KG.attackType, literal("Melee")))
        self.assertEqual(len(KnowledgeBase(self.graph).cq09(question)), 1)

    def test_bkb_query_treats_absent_passage_as_false(self):
        for identifier, internal, label, hero_id, hero_name in (
                (1, "lion_voodoo", "Hex", 26, "Lion"),
                (2, "beastmaster_primal_roar", "Primal Roar", 38, "Beastmaster")):
            ability, hero = RES[f"ability/{identifier}"], RES[f"hero/{hero_id}"]
            for s,p,o in ((ability,KG.internalName,literal(internal)), (ability,RDFS.label,literal(label)),
                          (ability,KG.hero,hero), (ability,RDF.type,KG.Ability), (hero,RDFS.label,literal(hero_name))):
                self.graph.add((s,p,o))
        self.graph.add((RES["ability/2"], KG.piercesDebuffImmunity, literal(True)))
        kb = KnowledgeBase(self.graph)
        actual = {row["ability"]: row["pierces"] for ability in (RES["ability/1"], RES["ability/2"])
                  for row in kb.cq08(Question(8, subject=ability))}
        self.assertEqual(actual, {str(RES["ability/1"]): False, str(RES["ability/2"]): True})


if __name__ == "__main__":
    unittest.main()
