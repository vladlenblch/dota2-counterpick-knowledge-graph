"""Answers must follow graph mutations, not preselected example identities."""
import io
import json
import unittest

from rdflib import Graph, RDF, RDFS

from dota_kg.answers import KnowledgeBase, print_answer
from dota_kg.ontology import literal
from dota_kg.ontology_schema import KG, RES
from dota_kg.questions import Question, QuestionParser


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
        for prop, value in ((KG.mostPopularPosition, 2), (KG.secondMostPopularPosition, 4)):
            self.add(self.hero, prop, literal(value))
        self.effect = self.effect_node(self.spell, "AOE_DAMAGE")
        self.target_effect = self.effect_node(self.target, "CREATES_ILLUSIONS")
        self.rule = self.node("rule/aoe", KG.CounterRule, "Area against illusions")
        self.add(self.rule, KG.sourceMechanic, RES["mechanic/AOE_DAMAGE"])
        self.add(self.rule, KG.targetMechanic, RES["mechanic/CREATES_ILLUSIONS"])
        self.add(self.rule, KG.ruleStatus, literal("heuristic"))
        self.add(self.rule, KG.ruleWeight, literal(1.))
        self.add(self.rule, KG.ruleFamily, literal('units'))
        self.add(self.rule, KG.sourceEffectTarget, literal('enemy'))

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
        self.add(effect, RDF.type, KG.Effect)
        recipient = 'area' if code == 'CREATES_ILLUSIONS' else 'self' if code in {'ARMOR_BUFF', 'BASIC_DISPEL', 'STRONG_DISPEL', 'LOW_ARMOR'} else 'enemy'
        self.add(effect, KG.effectTarget, literal(recipient))
        self.add(effect, KG.effectMechanic, RES["mechanic/" + code])
        self.add(effect, KG.evidence, literal("Fixture evidence"))
        return effect

    def ask(self, text, **kwargs):
        return KnowledgeBase(self.graph).ask(text, **kwargs)

    def test_aliases_cases_and_position(self):
        for name in ("ПЛ", "Фантом Лансера", "Phantom Lancer", "лансера"):
            result = self.ask(f"кто контрит {name} на миде")
            self.assertEqual(result["parameters"]["enemy"], str(self.enemy))
            self.assertEqual({r["role"] for r in result["answers"]}, {2})
            self.assertEqual(result["answers"][0]["hero"], str(self.hero))
        parser = QuestionParser(self.graph)
        self.assertEqual(parser.parse("кто контрит фантомку").enemy, self.pa)
        self.assertEqual(parser.parse("кто контрит ПЛ на хард саппорте").role, 5)
        self.assertEqual(parser.parse("кто контрит ПЛ на тройке").role, 3)

    def test_no_position_uses_only_two_popular_positions_without_legacy_fallback(self):
        result = self.ask("кто контрит ПЛ")
        self.assertEqual({r["role"] for r in result["answers"]}, {2, 4})
        self.assertEqual(result["missing_roles"], [1, 3, 5])
        self.graph.set((self.hero, KG.mostPopularPosition, literal(5)))
        result = self.ask("кто контрит ПЛ")
        self.assertEqual([r["role"] for r in result["answers"]], [4, 5])
        self.graph.remove((self.hero, KG.mostPopularPosition, None))
        self.graph.remove((self.hero, KG.secondMostPopularPosition, None))
        for prop in (KG.carryLevel, KG.nukerLevel, KG.supportLevel):
            self.graph.set((self.hero, prop, literal(3)))
        self.assertEqual(self.ask("кто контрит ПЛ")["answers"], [])

    def test_five_positions_and_specific_support_phrases_are_distinct(self):
        parser = QuestionParser(self.graph)
        for position, phrases in {1: ("на керри", "на позиции 1", "pos1"),
                                  2: ("на миде", "на 2-й позиции", "mid"),
                                  3: ("на тройке", "offlane", "на харде"),
                                  4: ("на четверке", "soft support", "на саппорте"),
                                  5: ("на пятерке", "hard support", "на полной поддержке")}.items():
            for phrase in phrases:
                with self.subTest(position=position, phrase=phrase):
                    self.assertEqual(parser.parse("кто контрит ПЛ " + phrase).role, position)
        for question in ("кто контрит ПЛ на позиции 6", "кто контрит ПЛ на керри и на миде"):
            with self.subTest(question=question), self.assertRaises(ValueError):
                parser.parse(question)
        self.assertIsNone(parser.parse("кто контрит ПЛ на 10 минуте").role)

    def test_carry_and_mid_and_the_two_support_positions_filter_separately(self):
        for primary, secondary in ((1, None), (2, 4), (4, 5)):
            self.graph.set((self.hero, KG.mostPopularPosition, literal(primary)))
            self.graph.remove((self.hero, KG.secondMostPopularPosition, None))
            if secondary is not None:
                self.graph.set((self.hero, KG.secondMostPopularPosition, literal(secondary)))
            for position in range(1, 6):
                with self.subTest(primary=primary, secondary=secondary, position=position):
                    result = self.ask(f"кто контрит ПЛ на позиции {position}")
                    self.assertEqual(bool(result['answers']), position in (primary, secondary))

    def test_explanation_names_target_ability_and_omits_footer(self):
        self.graph.set((self.target, RDFS.label, literal("Conjure Image")))
        result = self.ask("кто контрит ПЛ на миде")
        self.assertIn("против «Conjure Image»", result["answers"][0]["answer"])
        self.assertEqual(result["answers"][0]["target_abilities"], [str(self.target)])
        self.assertEqual(result["notes"], [])

    def test_rule_review_messages_are_hidden_but_gameplay_conditions_and_graph_facts_remain(self):
        boilerplate = 'Урон по области — кандидат; достаточность урона и стойкость иллюзий не оценены.'
        review = self.node('condition/review', KG.InteractionCondition, 'Review')
        self.add(self.rule, KG.requiresCondition, review)
        self.add(review, KG.conditionKind, literal('review'))
        self.add(review, KG.conditionText, literal(boilerplate))
        item = self.node('condition/item', KG.InteractionCondition, 'Upgrade required')
        self.add(self.effect, KG.requiresCondition, item)
        self.add(item, KG.conditionKind, literal('has-item'))
        self.add(item, KG.conditionText, literal('Требуется Aghanim\'s Shard.'))
        shard = self.node('item/shard', KG.Item, 'Shard')
        self.add(item, KG.conditionItem, shard)
        self.add(item, KG.conditionScope, literal('source'))
        gameplay = self.node('condition/gameplay', KG.InteractionCondition, 'Positioning')
        self.add(self.effect, KG.requiresCondition, gameplay)
        self.add(gameplay, KG.conditionKind, literal('review'))
        self.add(gameplay, KG.conditionText, literal('За целью должно быть дерево.'))
        result = self.ask('кто контрит ПЛ на миде', own_items=(shard,))
        self.assertNotIn(boilerplate, json.dumps(result, ensure_ascii=False))
        self.assertIn('Требуется Aghanim\'s Shard.', result['answers'][0]['answer'])
        self.assertIn('За целью должно быть дерево.', result['answers'][0]['answer'])
        self.assertEqual(self.graph.value(review, KG.conditionText), literal(boilerplate))

    def test_self_inflicted_control_is_not_a_counter_to_enemy_channeling(self):
        self.graph.set((self.rule, KG.sourceMechanic, RES['mechanic/SILENCE']))
        self.graph.set((self.rule, KG.targetMechanic, RES['mechanic/CHANNELING']))
        self.graph.set((self.target_effect, KG.effectMechanic, RES['mechanic/CHANNELING']))
        self.graph.set((self.effect, KG.effectMechanic, RES['mechanic/SILENCE']))
        self.graph.set((self.effect, KG.effectTarget, literal('self')))
        self.graph.set((RES['mechanic/SILENCE'], KG.mechanicCategory, literal('control')))
        self.assertEqual(self.ask('кто контрит ПЛ')['answers'], [])
        self.graph.set((self.effect, KG.effectTarget, literal('enemy')))
        self.assertTrue(self.ask('кто контрит ПЛ')['answers'])

    def test_refined_fact_replaces_coarse_answer_but_remains_conditional(self):
        self.graph.remove((self.effect, KG.effectTarget, None))  # Coarse historical description.
        detailed = self.node('effect/detailed', KG.Effect, 'Detailed')
        self.add(self.spell, KG.hasEffect, detailed)
        self.add(detailed, KG.effectMechanic, RES['mechanic/AOE_DAMAGE'])
        self.add(detailed, KG.effectTarget, literal('enemy'))
        self.add(detailed, KG.evidence, literal('Detailed evidence'))
        condition = self.node('condition/night', KG.InteractionCondition, 'Night')
        self.add(detailed, KG.requiresCondition, condition)
        self.add(condition, KG.conditionText, literal('Действует ночью.'))
        result = self.ask('кто контрит ПЛ на миде')
        self.assertEqual(result['answers'][0]['effect'], str(detailed))
        self.assertIn('Действует ночью.', result['answers'][0]['answer'])
        self.assertIn((self.spell, KG.hasEffect, self.effect), self.graph)

    def test_alternative_target_mechanisms_do_not_impose_each_others_upgrades(self):
        upgraded = self.node('effect/upgraded-illusions', KG.Effect, 'Upgraded illusions')
        self.add(self.target, KG.hasEffect, upgraded)
        self.add(upgraded, KG.effectMechanic, RES['mechanic/CREATES_ILLUSIONS'])
        self.add(upgraded, KG.effectTarget, literal('area'))
        condition = self.node('condition/upgrade', KG.InteractionCondition, 'Upgrade')
        self.add(upgraded, KG.requiresCondition, condition)
        self.add(condition, KG.conditionText, literal('Требуется Scepter.'))
        scepter = self.node('item/scepter', KG.Item, 'Scepter')
        self.add(condition, KG.conditionKind, literal('has-item'))
        self.add(condition, KG.conditionScope, literal('source'))
        self.add(condition, KG.conditionItem, scepter)
        answer = self.ask('кто контрит ПЛ на миде')['answers'][0]
        self.assertNotIn('Scepter', answer['answer'])
        self.assertEqual(answer['target_facts'], [str(self.target_effect)])
        self.graph.remove((self.target, KG.hasEffect, self.target_effect))
        self.assertFalse(self.ask('кто контрит ПЛ на миде')['answers'])
        answer = self.ask('кто контрит ПЛ на миде', enemy_items=(scepter,))['answers'][0]
        self.assertIn('У противника: Требуется Scepter.', answer['answer'])

    def test_dispel_answer_uses_only_conditions_of_the_dispel_effect(self):
        self.add(self.effect, KG.requiredDispel, literal('Strong'))
        cleanser = self.node('ability/cleanser', KG.Ability, 'Cleanser')
        self.add(cleanser, KG.dispelTarget, literal('ally'))
        self.effect_node(cleanser, 'STRONG_DISPEL')
        upgrade_heal = self.effect_node(cleanser, 'HEAL')
        condition = self.node('condition/heal-upgrade', KG.InteractionCondition, 'Upgrade')
        self.add(upgrade_heal, KG.requiresCondition, condition)
        self.add(condition, KG.conditionText, literal('Требуется Shard.'))
        rows = KnowledgeBase(self.graph).cq07(Question(7, subject=self.spell))
        self.assertTrue(rows)
        self.assertNotIn('Shard', rows[0]['answer'])

    def test_parameter_answer_omits_unmapped_condition_message(self):
        parameter = self.node('parameter/test', KG.Parameter, 'Damage')
        value = self.node('value/test', KG.ParameterValue, 'Damage value')
        self.add(self.spell, KG.hasParameter, parameter)
        self.add(parameter, KG.parameterName, literal('damage'))
        self.add(parameter, KG.hasBaseValue, value)
        self.add(value, KG.position, literal(0))
        self.add(value, KG.numericValue, literal(100))
        answer = self.ask('при каких условиях работает Test spell')['answers'][0]['answer']
        self.assertIn('damage=[100]', answer)
        self.assertNotIn('не размечены', answer)

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
        self.add(self.other, KG.mostPopularPosition, literal(2))
        self.effect_node(other_spell, "AOE_DAMAGE")
        for hero, rate in ((self.hero, .5), (self.other, .7), (self.pa, 1.0)):
            obs = RES[str(hero) + "/observation"]
            self.add(hero, KG.hasMatchup, obs)
            self.add(obs, KG.opponent, self.enemy)
            self.add(obs, KG.winRate, literal(rate))
            self.add(obs, KG.normalizedAdvantage, literal(rate - .5))
            self.add(obs, KG.matches, literal(100))
        rows = self.ask("кто контрит ПЛ на миде")["answers"]
        self.assertEqual([r["hero"] for r in rows], [str(self.other), str(self.hero)])
        result = self.ask("кто контрит ПЛ на миде")
        output = io.StringIO()
        print_answer(result, output)
        self.assertNotIn('%', output.getvalue())
        self.assertNotIn('Доля побед', output.getvalue())
        self.assertNotIn('win_rate', json.dumps(result))
        self.assertNotIn('matches', json.dumps(result))

    def test_item_priorities_use_core_for_mid_and_offlane_and_support_for_both_support_positions(self):
        item = self.node('item/test', KG.Item, 'Test item')
        for group, rank in (('carry', 1), ('core', 2), ('support', 3)):
            pref = self.node('preference/' + group, KG.ItemRolePreference, group)
            self.add(pref, KG.item, item)
            self.add(pref, KG.roleName, literal(group))
            self.add(pref, KG.preferenceRank, literal(rank))
        kb = KnowledgeBase(self.graph)
        for position, expected in ((1, 1), (2, 2), (3, 2), (4, 3), (5, 3)):
            with self.subTest(position=position):
                self.assertEqual(kb.item_priority(item, Question(9, hero=self.hero, role=position)), expected)

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
