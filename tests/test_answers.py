"""Answers must follow graph mutations, not preselected example identities."""
import unittest

from rdflib import Graph, RDF, RDFS

from dota_kg.answers import KnowledgeBase
from dota_kg.ontology import literal
from dota_kg.ontology_schema import KG, RES
from dota_kg.questions import QuestionParser


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.enemy = self.node("hero/enemy", KG.Hero, "Phantom Lancer")
        self.hero = self.node("hero/candidate", KG.Hero, "Test candidate")
        self.other = self.node("hero/other", KG.Hero, "Other candidate")
        self.pa = self.node("hero/pa", KG.Hero, "Phantom Assassin")
        self.spell = self.node("ability/candidate", KG.Ability, "Test spell")
        self.target = self.node("ability/enemy", KG.Ability, "Target spell")
        self.add(self.hero, KG.hasAbility, self.spell)
        self.add(self.enemy, KG.hasAbility, self.target)
        for prop, value in ((KG.carryLevel, 2), (KG.nukerLevel, 2),
                            (KG.supportLevel, 2), (KG.durableLevel, 2), (KG.initiatorLevel, 2)):
            self.add(self.hero, prop, literal(value))
        self.effect = self.effect_node(self.spell, "AOE_DAMAGE")
        self.target_effect = self.effect_node(self.target, "CREATES_ILLUSIONS")
        self.rule = self.node("rule/aoe", KG.CounterRule, "Area against illusions")
        self.add(self.rule, KG.sourceMechanic, RES["mechanic/AOE_DAMAGE"])
        self.add(self.rule, KG.targetMechanic, RES["mechanic/CREATES_ILLUSIONS"])
        self.add(self.rule, KG.ruleStatus, literal("heuristic"))

    def add(self, s, p, o):
        self.graph.add((s, p, o))

    def node(self, path, cls, label):
        node = RES[path]
        self.add(node, RDF.type, cls)
        self.add(node, RDFS.label, literal(label))
        return node

    def effect_node(self, owner, code):
        effect = RES[str(owner).split("resource/")[1] + "/effect/" + code]
        self.add(owner, KG.hasEffect, effect)
        self.add(effect, KG.effectMechanic, RES["mechanic/" + code])
        self.add(effect, KG.evidence, literal("Fixture evidence"))
        return effect

    def ask(self, text, **kwargs):
        return KnowledgeBase(self.graph).ask(text, **kwargs)

    def test_aliases_cases_and_position(self):
        for name in ("ПЛ", "Фантом Лансера", "Phantom Lancer", "лансера"):
            result = self.ask(f"кто контрит {name} на миде")
            self.assertEqual(result["parameters"]["enemy"], str(self.enemy))
            self.assertEqual({r["role"] for r in result["answers"]}, {"carry"})
            self.assertEqual(result["answers"][0]["hero"], str(self.hero))
        parser = QuestionParser(self.graph)
        self.assertEqual(parser.parse("кто контрит фантомку").enemy, self.pa)
        self.assertEqual(parser.parse("кто контрит ПЛ на хард саппорте").role, "support")
        self.assertEqual(parser.parse("кто контрит ПЛ на тройке").role, "nuker")

    def test_no_position_returns_three_graph_roles(self):
        result = self.ask("кто контрит ПЛ")
        self.assertEqual({r["role"] for r in result["answers"]}, {"carry", "nuker", "support"})
        self.graph.set((self.hero, KG.carryLevel, literal(0)))
        self.graph.set((self.hero, KG.nukerLevel, literal(0)))
        result = self.ask("кто контрит ПЛ")
        self.assertEqual([r["role"] for r in result["answers"]], ["support"])
        self.assertEqual(len(result["missing_roles"]), 2)

    def test_explanation_names_target_ability_and_omits_footer(self):
        self.graph.set((self.target, RDFS.label, literal("Conjure Image")))
        result = self.ask("кто контрит ПЛ на миде")
        self.assertIn("против «Conjure Image»", result["answers"][0]["answer"])
        self.assertEqual(result["answers"][0]["target_abilities"], [str(self.target)])
        self.assertEqual(result["notes"], [])

    def test_removing_rule_or_target_effect_removes_answer(self):
        self.assertTrue(self.ask("кто контрит ПЛ")["answers"])
        self.graph.remove((self.target, KG.hasEffect, self.target_effect))
        self.assertEqual(self.ask("кто контрит ПЛ")["status"], "no_data")
        self.add(self.target, KG.hasEffect, self.target_effect)
        self.graph.remove((self.rule, KG.sourceMechanic, None))
        self.assertEqual(self.ask("кто контрит ПЛ")["answers"], [])

    def test_unobserved_features_never_supply_target(self):
        self.graph.remove((self.target, KG.hasEffect, None))
        feature = self.node("feature/illusion", KG.Feature, "Illusions")
        fact = RES["fact/illusion"]
        self.add(self.enemy, KG.hasFeatureValue, fact)
        self.add(fact, KG.feature, feature)
        self.add(feature, KG.featureMechanic, RES["mechanic/CREATES_ILLUSIONS"])
        self.add(fact, KG.value, literal(1.0))
        self.add(fact, KG.isObserved, literal(False))
        self.assertFalse(self.ask("кто контрит ПЛ")["answers"])
        self.graph.set((fact, KG.isObserved, literal(True)))
        self.assertTrue(self.ask("кто контрит ПЛ")["answers"])
        self.graph.set((fact, KG.value, literal(0.0)))
        self.assertFalse(self.ask("кто контрит ПЛ")["answers"])

    def test_statistic_sorts_only_graph_supported_candidates(self):
        other_spell = self.node("ability/other", KG.Ability, "Other spell")
        self.add(self.other, KG.hasAbility, other_spell)
        self.add(self.other, KG.carryLevel, literal(1))
        self.effect_node(other_spell, "AOE_DAMAGE")
        for hero, rate in ((self.hero, .5), (self.other, .7), (self.pa, 1.0)):
            obs = RES[str(hero) + "/observation"]
            self.add(hero, KG.hasMatchup, obs)
            self.add(obs, KG.opponent, self.enemy)
            self.add(obs, KG.winRate, literal(rate))
            self.add(obs, KG.matches, literal(100))
        rows = self.ask("кто контрит ПЛ на миде")["answers"]
        self.assertEqual([r["hero"] for r in rows], [str(self.other), str(self.hero)])

    def test_unknown_and_ambiguous_names_and_tasks_are_rejected(self):
        for question in ("кто контрит несуществующего героя", "какая сегодня погода", "почему ПЛ"):
            with self.subTest(question=question), self.assertRaises(ValueError):
                self.ask(question)
        self.add(self.pa, RDFS.label, literal("Phantom Lancer"))
        with self.assertRaisesRegex(ValueError, "Неоднозначное"):
            self.ask("кто контрит пл")

    def test_twelve_task_routes(self):
        examples = {
            1: "кто контрит ПЛ", 2: "почему Test candidate контрит ПЛ",
            3: "при каких условиях работает Test spell", 4: "чем прервать Target spell",
            5: "что помогает против иллюзий ПЛ", 6: "чем обнаруживать невидимых противников",
            7: "какие эффекты ПЛ можно снять", 8: "какие способности ПЛ действуют сквозь BKB",
            9: "что купить Test candidate против ПЛ", 10: "какие уязвимости Test candidate компенсировать",
            11: "как Test candidate контрит ПЛ с активным BKB у противника",
            12: "что шард меняет у Test candidate",
        }
        bkb = self.node("item/bkb", KG.Item, "Black King Bar")
        self.assertIsNotNone(bkb)
        for cq, question in examples.items():
            with self.subTest(cq=cq):
                self.assertEqual(self.ask(question)["cq"], cq)

    def test_dispel_uses_strength_and_scope(self):
        self.add(self.target_effect, KG.requiredDispel, literal("Strong"))
        basic = self.node("item/basic", KG.Item, "Basic cleanser")
        strong = self.node("ability/strong", KG.Ability, "Strong cleanser")
        self.effect_node(basic, "BASIC_DISPEL")
        self.effect_node(strong, "STRONG_DISPEL")
        self.add(basic, KG.dispelTarget, literal("self"))
        self.add(strong, KG.dispelTarget, literal("ally"))
        rows = self.ask("какие эффекты ПЛ можно снять")["answers"]
        self.assertEqual([r["cleanser"] for r in rows], [str(strong)])

    def test_item_applicability_and_priority_are_graph_driven(self):
        item = self.node("item/aoe", KG.Item, "Area item")
        self.add(item, KG.isPurchasable, literal(True))
        effect = self.effect_node(item, "AOE_DAMAGE")
        condition = RES["condition/melee"]
        self.add(effect, KG.requiresCondition, condition)
        self.add(condition, KG.conditionKind, literal("has-mechanic"))
        self.add(condition, KG.conditionScope, literal("source"))
        self.add(condition, KG.conditionMechanic, RES["mechanic/MELEE"])
        self.assertFalse(self.ask("что купить Test candidate против ПЛ")["answers"])
        self.effect_node(self.spell, "MELEE")
        self.assertEqual(self.ask("что купить Test candidate против ПЛ")["answers"][0]["item"], str(item))

    def test_immunity_and_enemy_item_activation_are_distinct(self):
        bkb = self.node("item/bkb", KG.Item, "Black King Bar")
        self.effect_node(bkb, "DEBUFF_IMMUNITY")
        blocker = RES["condition/immunity"]
        self.add(self.rule, KG.blockedByCondition, blocker)
        self.add(blocker, KG.conditionMechanic, RES["mechanic/DEBUFF_IMMUNITY"])
        base = "как Test candidate контрит ПЛ если у противника BKB"
        row = self.ask(base)["answers"][0]
        self.assertIsNone(row["active"])
        self.assertIn("уточните", row["answer"])
        row = self.ask(base + " не активирован")["answers"][0]
        self.assertIs(row["active"], False)
        self.add(self.spell, KG.piercesDebuffImmunity, literal(True))
        row = self.ask(base + " активен")["answers"][0]
        self.assertIs(row["active"], True)
        self.assertTrue(row["pierces"])

    def test_list_immunity_excludes_nonpiercing_but_specific_check_reports_false(self):
        self.assertEqual(self.ask("какие способности ПЛ проходят сквозь BKB")["answers"], [])
        checked = self.ask("проходит ли Target spell сквозь BKB")["answers"]
        self.assertEqual([r["pierces"] for r in checked], [False])
        self.add(self.target, KG.piercesDebuffImmunity, literal(True))
        self.assertEqual([r["pierces"] for r in self.ask("какие способности ПЛ проходят сквозь BKB")["answers"]], [True])

    def test_named_purchase_phase_requires_graph_preference(self):
        item = self.node("item/area", KG.Item, "Area item")
        self.add(item, KG.isPurchasable, literal(True))
        self.effect_node(item, "AOE_DAMAGE")
        pref = self.node("preference/late", KG.ItemRolePreference, "Late preference")
        self.add(pref, KG.item, item)
        self.add(pref, KG.roleName, literal("carry"))
        self.add(pref, KG.purchasePhase, literal("late"))
        self.add(pref, KG.preferenceRank, literal(3))
        self.assertFalse(self.ask("что купить Test candidate против ПЛ в начале игры")["answers"])
        self.assertTrue(self.ask("что купить Test candidate против ПЛ в поздней игре")["answers"])
        self.assertTrue(self.ask("что купить Test candidate против ПЛ")["answers"])

    def test_compensation_rule_is_not_used_to_counter_enemy(self):
        item = self.node("item/armor", KG.Item, "Armor item")
        self.add(item, KG.isPurchasable, literal(True))
        self.effect_node(item, "ARMOR_BUFF")
        self.effect_node(self.spell, "LOW_ARMOR")
        self.effect_node(self.target, "LOW_ARMOR")
        rule = self.node("rule/compensate", KG.CounterRule, "Compensate low armor")
        self.add(rule, KG.sourceMechanic, RES["mechanic/ARMOR_BUFF"])
        self.add(rule, KG.targetMechanic, RES["mechanic/LOW_ARMOR"])
        self.add(rule, KG.rulePurpose, literal("compensate"))
        self.assertFalse(self.ask("что купить Test candidate против ПЛ")["answers"])
        self.assertEqual([r["item"] for r in self.ask("какие уязвимости Test candidate компенсировать")["answers"]], [str(item)])


if __name__ == "__main__":
    unittest.main()
