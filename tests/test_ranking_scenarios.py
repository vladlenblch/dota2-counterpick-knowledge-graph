"""Regression scenarios and generic SPARQL/data parity for the saved snapshot."""
import json
import math
from pathlib import Path
import unittest

import pyarrow.parquet as pq
from rdflib import Literal, RDF, RDFS
from rdflib.plugins.sparql import prepareQuery

from dota_kg.answers import KnowledgeBase
from dota_kg.ontology import load_ontology
from dota_kg.ontology_schema import KG
from dota_kg.questions import QuestionParser
from dota_kg.ranking import RankingModel
from dota_kg.ranking_sparql import ranking_query

ROOT = Path(__file__).resolve().parents[1]


class RankingScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_ontology(ROOT/'ontology', cache=True)
        cls.model = RankingModel(cls.graph)
        cls.parser = QuestionParser(cls.graph)

    def entity(self, name, cls=KG.Hero):
        return self.parser.resolve_entity(name, cls)

    def test_eight_reviewed_comparisons(self):
        # Names belong to test expectations only, never to ranking code or queries.
        for enemy, position, expected in (
            ('Phantom Lancer', 1, ['Leshrac', 'Sven', 'Anti-Mage']),
            ('Phantom Lancer', 2, ['Earthshaker', 'Leshrac', 'Sand King']),
            ('Phantom Lancer', 3, ['Axe', 'Sand King', 'Timbersaw']),
            ('Phantom Lancer', None, ['Earthshaker', 'Axe', 'Leshrac', 'Sven', 'Sand King', 'Timbersaw']),
            ('Huskar', None, ['Viper', 'Necrophos', 'Ancient Apparition']),
            ('Bristleback', None, ['Viper', 'Legion Commander', 'Hoodwink']),
            ('Morphling', None, ['Ancient Apparition', 'Doom', 'Lion']),
            ('Legion Commander', None, ['Winter Wyvern', 'Oracle', 'Shadow Demon'])):
            with self.subTest(enemy=enemy, position=position):
                results = self.model.rank(self.entity(enemy))
                if position:
                    results = [r for r in results if position in KnowledgeBase(self.graph).roles(r.hero)]
                names = [str(self.graph.value(r.hero, RDFS.label)) for r in results[:len(expected)]]
                self.assertEqual(names, expected)

    def test_all_enemies_and_positions_have_finite_graph_supported_candidates(self):
        kb = KnowledgeBase(self.graph)
        contexts = 0
        for enemy in self.model.heroes:
            ranked = self.model.rank(enemy)
            self.assertTrue(ranked)
            for row in ranked:
                self.assertNotEqual(row.hero, enemy)
                self.assertTrue(math.isfinite(row.score))
                self.assertTrue(row.primary.need.facts)
                self.assertIn((row.primary.fact.owner, KG.hasEffect, row.primary.fact.uri), self.graph)
            for position in range(1, 6):
                candidates = [r for r in ranked if position in kb.roles(r.hero)]
                self.assertTrue(candidates, (enemy, position))
                contexts += 1
        self.assertEqual(contexts, 635)

    def test_explicit_items_change_scoring_without_assuming_them_for_every_hero(self):
        enemy, anti_mage = self.entity('Phantom Lancer'), self.entity('Anti-Mage')
        bf = self.entity('Battle Fury', KG.Item)
        base = self.model.assess(anti_mage, enemy)
        equipped = RankingModel(self.graph, own_items=(bf,)).assess(anti_mage, enemy)
        self.assertGreater(equipped.score, base.score)
        self.assertTrue(any(a.fact.owner == bf for g in equipped.groups for a in g['applications']))
        self.assertFalse(any(a.fact.owner in self.model.items for g in base.groups for a in g['applications']))
        ranged = self.entity('Drow Ranger')
        apps = RankingModel(self.graph, own_items=(bf,)).applications(ranged, enemy)
        self.assertFalse(any(a.fact.owner == bf and a.fact.code == 'CLEAVE_DAMAGE' for a in apps))

    def test_weight_dataset_is_present_in_rdf_without_new_classes(self):
        rules = pq.read_table(ROOT/'data/counter_rules.parquet').to_pylist()
        self.assertEqual(sum(r['purpose'] == 'counter' for r in rules), 80)
        self.assertGreater(sum(r['purpose'] == 'purchase' for r in rules), 0)
        exported = {(r.source, r.target): r for r in self.model.rules if r.purpose in {'counter', 'purchase'}}
        for row in rules:
            actual = exported[row['source_mechanic'], row['target_mechanic']]
            self.assertEqual(actual.weight, row['weight'])
            self.assertEqual(actual.purpose, row['purpose'])
            self.assertEqual(actual.family, row['family'])
            self.assertEqual(actual.scopes, frozenset(json.loads(row['source_scopes'])))
        report = json.loads((ROOT/'ontology/validation.json').read_text())
        self.assertEqual(report['package_structure']['named_classes'], 21)
        self.assertTrue(report['conforms'])

    def test_four_queries_return_rdf_lineage_and_scores_match_text_search(self):
        enemy, hero = self.entity('Phantom Lancer'), self.entity('Axe')
        before = len(self.graph)
        with ranking_query(self.graph) as view:
            for name, bindings in (
                ('01_counterpicks.rq', {'enemy': enemy, 'position': Literal(3)}),
                ('02_explanations.rq', {'enemy': enemy, 'hero': hero}),
                ('03_conditions.rq', {'subject': self.entity('Shackleshot', KG.Ability)}),
                ('05_illusions.rq', {'enemy': enemy})):
                with self.subTest(query=name):
                    query = prepareQuery((ROOT/'queries'/name).read_text())
                    rows = list(view.query(query, initBindings=bindings))
                    self.assertTrue(rows)
                    if name.startswith('01'):
                        text = KnowledgeBase(self.graph).ask('кто контрит Phantom Lancer на позиции 3')
                        self.assertEqual([str(r.hero) for r in rows[:3]], [r['hero'] for r in text['answers']])
                        for sparql, answer in zip(rows, text['answers']):
                            self.assertAlmostEqual(float(sparql.score), answer['score'])
                            self.assertEqual(str(sparql.rule), answer['rule'])
                    if name.startswith('02'):
                        self.assertAlmostEqual(float(rows[0].score), round(self.model.assess(hero, enemy).score, 6))
                    if name.startswith('05'):
                        self.assertTrue(all(r.score.toPython() > 0 for r in rows))
        self.assertEqual(len(self.graph), before)

    def test_personalized_item_scenarios_and_native_hero_rankings(self):
        from dota_kg.item_ranking import ItemRanking
        ranker = ItemRanking(self.model)
        def names(hero, enemy, phase=None):
            rows = ranker.rank(self.entity(hero), self.entity(enemy), phase=phase)
            return [str(self.graph.value(r.item, RDFS.label)) for r in rows[:3]]
        for hero in ('Rubick', 'Lion', 'Crystal Maiden'):
            self.assertEqual(names(hero, 'Phantom Lancer'), ['Force Staff', 'Glimmer Cape', 'Ghost Scepter'])
        self.assertEqual(names('Anti-Mage', 'Phantom Lancer')[0], 'Battle Fury')
        sven = set(names('Sven', 'Phantom Lancer'))
        self.assertTrue({'Black King Bar', 'Monkey King Bar'} <= sven)
        self.assertTrue(sven <= {'Black King Bar', 'Monkey King Bar', 'Satanic'})
        self.assertEqual(names('Drow Ranger', 'Phantom Lancer')[0], 'Hurricane Pike')
        self.assertEqual(names('Phantom Assassin', 'Bristleback')[0], 'Silver Edge')
        self.assertEqual(names('Anti-Mage', 'Morphling')[0], 'Abyssal Blade')
        drow_options = ranker.rank(self.entity('Drow Ranger'), self.entity('Phantom Assassin'))
        # MKB is a valid situational alternative, despite zero personal purchases
        # in this snapshot; common fitting purchases can precede it.
        self.assertIn('Monkey King Bar', [str(self.graph.value(r.item, RDFS.label)) for r in drow_options[:5]])
        self.assertFalse({'Witch Blade', 'Parasma'} & set(names('Drow Ranger', 'Phantom Assassin')))
        self.assertIn('Aeon Disk', names('Crystal Maiden', 'Legion Commander'))
        for phase in (None, 'late'):
            self.assertNotIn('Battle Fury', names('Sven', 'Phantom Lancer', phase))
            self.assertNotIn('Scythe of Vyse', names('Anti-Mage', 'Phantom Lancer', phase))
            self.assertFalse({'Radiance', 'Mjollnir', 'Battle Fury'} & set(names('Rubick', 'Phantom Lancer', phase)))
        # Same approved hero score despite new purchase-only rules and item fixes.
        self.assertAlmostEqual(self.model.assess(self.entity('Axe'), self.entity('Phantom Lancer')).score, 6.808952, places=5)

    def test_personalized_item_sparql_uses_the_same_score_and_suitability(self):
        from dota_kg.item_ranking import ItemRanking
        hero, enemy = self.entity('Rubick'), self.entity('Phantom Lancer')
        focus = {'units', 'GROUPED_UNITS', 'ILLUSIONS'}
        expected = {r.item: r for r in ItemRanking(self.model).rank(hero, enemy, focus=focus)}
        query = prepareQuery((ROOT/'queries/05_illusions.rq').read_text())
        with ranking_query(self.graph, actor=hero) as view:
            rows = list(view.query(query, initBindings={'hero': hero, 'enemy': enemy}))
        items = [r for r in rows if r.source in self.model.items]
        self.assertEqual({r.source for r in items}, set(expected))
        for row in items:
            self.assertAlmostEqual(float(row.score), round(expected[row.source].score, 6))
            self.assertEqual(row.rule, expected[row.source].primary.rule.uri)

    def test_item_results_for_all_enemies_are_finite_and_actual_purchasable_items(self):
        from dota_kg.item_ranking import ItemRanking
        ranker = ItemRanking(self.model)
        checked = 0
        for name in ('Anti-Mage', 'Rubick', 'Tidehunter'):
            hero = self.entity(name)
            for enemy in self.model.heroes:
                if hero == enemy:
                    continue
                rows = ranker.rank(hero, enemy)
                for row in rows:
                    self.assertTrue(math.isfinite(row.score) and row.score > 0)
                    self.assertTrue(ranker.purchasable(row.item))
                    self.assertTrue(row.primary.need.facts)
                checked += 1
        self.assertEqual(checked, 378)


if __name__ == '__main__':
    unittest.main()
