"""Data-driven scoring and the CLI SPARQL bridge on small independent graphs."""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql import operators

from dota_kg.ontology import literal
from dota_kg.ontology_queries import run_query
from dota_kg.ontology_schema import KG, RES
from dota_kg.ranking import RankingModel, STAT_LIMIT, REVERSE_WEIGHT
from dota_kg.ranking_sparql import FN, FUNCTIONS, ranking_query

ROOT = Path(__file__).resolve().parents[1]


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.g = Graph()
        self.hero = self.node('hero/source', KG.Hero, 'Source hero')
        self.enemy = self.node('hero/target', KG.Hero, 'Target hero')
        self.spell = self.node('ability/source', KG.Ability, 'Source spell')
        self.target = self.node('ability/target', KG.Ability, 'Target spell')
        self.g.add((self.hero, KG.hasAbility, self.spell))
        self.g.add((self.enemy, KG.hasAbility, self.target))
        self.g.add((self.hero, KG.mostPopularPosition, literal(3)))
        self.effect = self.effect_node(self.spell, 'TEST_PRESSURE', 'enemy')
        self.effect_node(self.target, 'TEST_DEFENSE', 'self')
        self.rule = self.rule_node('TEST_PRESSURE', 'TEST_DEFENSE', 'test', 2.)

    def node(self, path, cls, label):
        uri = RES[path]
        self.g.add((uri, RDF.type, cls))
        self.g.add((uri, RDFS.label, literal(label)))
        return uri

    def effect_node(self, owner, code, scope):
        uri = URIRef(str(owner) + '/effect/' + code)
        self.g.add((owner, KG.hasEffect, uri))
        self.g.add((uri, RDF.type, KG.Effect))
        self.g.add((uri, KG.effectMechanic, RES['mechanic/' + code]))
        self.g.add((uri, KG.effectTarget, literal(scope)))
        self.g.add((uri, KG.evidence, literal('Gameplay evidence')))
        return uri

    def rule_node(self, source, target, family, weight, scope='enemy'):
        rule = self.node('rule/' + source + '/' + target, KG.CounterRule, family)
        for p, v in ((KG.sourceMechanic, RES['mechanic/' + source]),
                     (KG.targetMechanic, RES['mechanic/' + target]),
                     (KG.ruleWeight, literal(weight)), (KG.ruleFamily, literal(family)),
                     (KG.sourceEffectTarget, literal(scope))):
            self.g.add((rule, p, v))
        return rule

    def score(self, **kwargs):
        return RankingModel(self.g, **kwargs).assess(self.hero, self.enemy)

    def test_duplicates_insertion_order_and_entity_renaming_do_not_inflate_score(self):
        before = self.score().score
        copy = URIRef(str(self.effect) + '/duplicate')
        for _, p, o in list(self.g.triples((self.effect, None, None))):
            self.g.add((copy, p, o))
        self.g.add((self.spell, KG.hasEffect, copy))
        self.assertEqual(self.score().score, before)
        renamed = Graph()
        entities = {self.hero, self.enemy, self.spell, self.target, self.effect, copy}
        mapping = {node: URIRef('urn:renamed:' + str(i)) for i, node in enumerate(sorted(entities))}
        for s, p, o in reversed(list(self.g)):
            renamed.add((mapping.get(s, s), p, mapping.get(o, o)))
        result = RankingModel(renamed).assess(mapping[self.hero], mapping[self.enemy])
        self.assertEqual(result.score, before)

    def test_independent_abilities_and_reused_families_have_diminishing_returns(self):
        self.effect_node(self.spell, 'SECOND_PRESSURE', 'enemy')
        self.rule_node('SECOND_PRESSURE', 'TEST_DEFENSE', 'second', 1.)
        self.assertAlmostEqual(self.score().score, 2.1)  # .4 * .25 for the reused spell.
        extra = self.node('ability/extra', KG.Ability, 'Extra spell')
        self.g.add((self.hero, KG.hasAbility, extra))
        self.effect_node(extra, 'TEST_PRESSURE', 'enemy')
        result = self.score()
        self.assertAlmostEqual(result.mechanical, 2.7)  # 2 + .3*2 + .4*.25*1
        self.assertEqual(len(result.groups[0]['applications']), 2)

    def test_statistics_are_bounded_and_never_create_a_mechanical_candidate(self):
        obs = RES['matchup/test']
        self.g.add((self.hero, KG.hasMatchup, obs))
        self.g.add((obs, KG.opponent, self.enemy))
        self.g.add((obs, KG.matches, literal(1000000)))
        self.g.add((obs, KG.normalizedAdvantage, literal(1000.)))
        self.assertLessEqual(abs(self.score().statistical), STAT_LIMIT)
        self.assertAlmostEqual(self.score().mechanical, 2.)
        self.g.remove((self.rule, KG.sourceMechanic, None))
        self.assertIsNone(self.score())

    def test_opponent_counterplay_reduces_the_score(self):
        self.rule_node('TEST_DEFENSE', 'TEST_PRESSURE', 'reverse', 1., scope='self')
        result = self.score()
        self.assertAlmostEqual(result.reverse, 1.)
        self.assertAlmostEqual(result.score, 2. - REVERSE_WEIGHT)

    def test_unowned_upgrades_and_self_control_are_not_enemy_counterplay(self):
        shard = self.node('item/upgrade', KG.Item, 'Upgrade')
        condition = self.node('condition/upgrade', KG.InteractionCondition, 'Requires upgrade')
        for s, p, o in ((self.effect, KG.requiresCondition, condition),
                        (condition, KG.conditionKind, literal('has-item')),
                        (condition, KG.conditionScope, literal('source')),
                        (condition, KG.conditionItem, shard)):
            self.g.add((s, p, o))
        self.assertIsNone(self.score())
        self.assertIsNotNone(self.score(own_items=(shard,)))
        self.g.set((self.effect, KG.effectTarget, literal('self')))
        self.assertIsNone(self.score(own_items=(shard,)))

    def test_break_preserves_stacks_qualifies_only_the_affected_passive(self):
        self.g.set((self.effect, KG.effectMechanic, RES['mechanic/BREAK']))
        self.g.remove((self.target, KG.hasEffect, None))
        self.effect_node(self.target, 'PASSIVE_DEFENSE', 'self')
        self.g.remove((self.rule, None, None))
        self.rule_node('BREAK', 'PASSIVE_DEFENSE', 'passive', 2.)
        baseline = self.score().score
        unrelated = self.node('ability/unrelated', KG.Ability, 'Unrelated passive')
        self.g.add((self.enemy, KG.hasAbility, unrelated))
        self.effect_node(unrelated, 'BREAK_PRESERVES_STACKS', 'self')
        self.assertEqual(self.score().score, baseline)
        self.effect_node(self.target, 'BREAK_PRESERVES_STACKS', 'self')
        self.assertAlmostEqual(self.score().score, baseline * .45)

    def test_invalid_weight_and_unknown_context_are_rejected(self):
        for weight in (0., -1., float('nan'), float('inf')):
            self.g.set((self.rule, KG.ruleWeight, Literal(weight)))
            with self.assertRaises(ValueError):
                self.score()
        self.g.set((self.rule, KG.ruleWeight, literal(2.)))
        with self.assertRaises(ValueError):
            self.score(level=0)
        with self.assertRaises(ValueError):
            self.score(own_items=(URIRef('urn:unknown-item'),))

    def execute(self, path, **kwargs):
        output = io.StringIO()
        with patch('dota_kg.ontology.load_ontology', return_value=self.g):
            run_query(path, directory=ROOT/'ontology', output_format='json',
                      output=output, progress=io.StringIO(), **kwargs)
        return json.loads(output.getvalue())['results']['bindings']

    def test_generic_sparql_matches_model_with_typed_bindings_and_keeps_graph_unchanged(self):
        before = set(self.g)
        rows = self.execute(ROOT/'queries/01_counterpicks.rq',
                            bindings={'enemy': self.enemy, 'position': 3})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['hero']['value'], str(self.hero))
        self.assertEqual(float(rows[0]['score']['value']), self.score().score)
        self.assertEqual(set(self.g), before)
        self.assertEqual(self.execute(ROOT/'queries/01_counterpicks.rq',
                         bindings={'enemy': self.enemy, 'position': 5}), [])
        rows = self.execute(ROOT/'queries/02_explanations.rq',
                            bindings={'hero': 'Source hero', 'enemy': 'Target hero'})
        self.assertEqual(rows[0]['rule']['value'], str(self.rule))

    def test_names_shared_by_heroes_and_abilities_resolve_by_requested_type(self):
        from dota_kg.questions import QuestionParser
        self.g.set((self.target, RDFS.label, literal('Target hero')))
        parser = QuestionParser(self.g)
        self.assertEqual(parser.resolve_entity('Target hero', KG.Hero), self.enemy)
        self.assertEqual(parser.resolve_entity('Target hero', KG.Ability), self.target)
        self.assertEqual(parser.parse('кто контрит Target hero').enemy, self.enemy)
        rows = self.execute(ROOT/'queries/01_counterpicks.rq', bindings={'enemy': 'Target hero'})
        self.assertEqual(rows[0]['hero']['value'], str(self.hero))

    def test_item_as_main_reason_still_names_the_recommended_hero(self):
        from dota_kg.answers import KnowledgeBase
        item = self.node('item/explicit', KG.Item, 'Explicit item')
        self.effect_node(item, 'ITEM_PRESSURE', 'enemy')
        self.rule_node('ITEM_PRESSURE', 'TEST_DEFENSE', 'test', 4.)
        result = KnowledgeBase(self.g).ask('кто контрит Target hero на позиции 3', own_items=(item,))
        answer = result['answers'][0]
        self.assertEqual(answer['source'], str(item))
        self.assertIn('Source hero', answer['answer'])
        self.assertIn('Explicit item', answer['answer'])
        self.assertAlmostEqual(sum(r['contribution'] for r in answer['reasons']), answer['mechanical_score'])

    def test_custom_functions_are_removed_even_after_exceptions(self):
        before = {key: value for key, value in operators._CUSTOM_FUNCTIONS.items() if key in FUNCTIONS}
        with self.assertRaisesRegex(RuntimeError, 'test interruption'):
            with ranking_query(self.g) as view:
                query = prepareQuery(f'SELECT ?score WHERE {{ BIND(<{FN.score}>(<{self.hero}>, <{self.enemy}>) AS ?score) }}')
                self.assertEqual(float(next(iter(view.query(query))).score), 2.)
                raise RuntimeError('test interruption')
        after = {key: value for key, value in operators._CUSTOM_FUNCTIONS.items() if key in FUNCTIONS}
        self.assertEqual(before, after)
        self.g.set((self.rule, KG.ruleWeight, literal(4.)))
        with ranking_query(self.g) as view:
            self.assertEqual(float(next(iter(view.query(query))).score), 4.)


if __name__ == '__main__':
    unittest.main()
