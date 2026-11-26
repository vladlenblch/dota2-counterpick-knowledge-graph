"""Source fidelity, activation conditions and meaningful mechanic regressions."""
from copy import deepcopy
from pathlib import Path
import json
import unittest

from rdflib import Graph, RDF, RDFS

from dota_kg.ontology import entity, literal, read_tables
from dota_kg.ontology_interactions import build_interactions, validate_interactions
from dota_kg.ontology_mechanics import checked_mechanics, build_gameplay_mechanics
from dota_kg.ontology_schema import KG, RES, schema_graph

ROOT = Path(__file__).resolve().parent.parent


class GameplayMechanicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = read_tables(ROOT)
        cls.abilities = {a['internal_name']: a for a in cls.rows['abilities']}
        cls.items = {i['internal_name']: i for i in cls.rows['items']}

    def facts(self, name, code=None):
        row = self.abilities.get(name, self.items.get(name))
        kind = 'ability' if name in self.abilities else 'item'
        key = row['ability_id'] if kind == 'ability' else row['item_id']
        return [f for f in self.rows['gameplay_mechanics']
                if f['entity_kind'] == kind and f['entity_id'] == key
                and (code is None or f['mechanic'] == code)]

    def subset(self, name, code):
        facts = deepcopy(self.facts(name, code))
        return {**self.rows, 'gameplay_mechanics': facts}

    def test_entire_dataset_is_source_backed_and_all_heroes_are_represented(self):
        catalog, facts = checked_mechanics(self.rows)
        ability_ids = {f['entity_id'] for f, _ in facts if f['entity_kind'] == 'ability'}
        hero_ids = {a['hero_id'] for a in self.rows['abilities'] if a['ability_id'] in ability_ids}
        self.assertEqual(hero_ids, {h['hero_id'] for h in self.rows['heroes']})
        self.assertGreater(len(catalog), 100)
        self.assertGreater(len(facts), 2000)

    def test_key_defenses_resources_and_ally_saves_are_not_lost(self):
        expected = {
            'huskar_berserkers_blood': 'HEALTH_REGEN LOW_HEALTH_SCALING PASSIVE_DEFENSE',
            'bristleback_bristleback': 'DIRECTIONAL_DEFENSE ATTACK_TRIGGERED_AOE',
            'morphling_morph_str': 'ATTRIBUTE_SHIFT',
            'legion_commander_duel': 'FORCED_COMBAT FORCED_FACING',
            'oracle_false_promise': 'ALLY_DAMAGE_DELAY ALLY_SAVE',
            'winter_wyvern_cold_embrace': 'ALLY_PHYSICAL_IMMUNITY ALLY_SAVE',
            'shadow_demon_disruption': 'ALLY_BANISH ALLY_SAVE',
            'axe_culling_blade': 'HEALTH_EXECUTION',
            'rubick_spell_steal': 'ABILITY_STEAL',
            'arc_warden_tempest_double': 'ABILITY_COPY ITEM_DUPLICATION CLONES',
            'lone_druid_spirit_bear': 'SUMMON_INVENTORY',
            'ogre_magi_multicast': 'SPELL_MULTICAST',
            'slark_dark_pact': 'CONTINUOUS_DISPEL',
            'weaver_time_lapse': 'HEALTH_MANA_RESET',
            'item_bfury': 'CLEAVE_DAMAGE',
            'item_mjollnir': 'CHAIN_LIGHTNING RETALIATORY_LIGHTNING',
            'item_lotus_orb': 'SPELL_REFLECTION ALLY_DISPEL',
        }
        for name, codes in expected.items():
            with self.subTest(entity=name):
                self.assertTrue(set(codes.split()) <= {f['mechanic'] for f in self.facts(name)})

    def test_mentions_of_protection_costs_and_exceptions_do_not_become_enemy_effects(self):
        rejected = {
            'item_smoke_of_deceit': 'DETECTION', 'techies_land_mines': 'DETECTION',
            'winter_wyvern_cold_embrace': 'MAX_HEALTH_DAMAGE',
            'morphling_morph_str': 'FORCED_MOVEMENT STUN',
            'luna_eclipse': 'STUN', 'lycan_howl': 'FEAR',
            'winter_wyvern_arctic_burn': 'MANA_BURN MANA_DRAIN',
            'oracle_purifying_flames': 'AOE_DAMAGE',
            'oracle_false_promise': 'ALLY_DEATH_PREVENTION',
            'phoenix_dying_light': 'MISSING_HEALTH_DAMAGE',
            'alchemist_chemical_rage': 'ANTI_HEAL',
            'lycan_wolf_bite': 'ANTI_HEAL',
            'lycan_summon_wolves': 'PERMANENT_SCALING',
            'item_black_king_bar': 'MAGIC_RESIST_REDUCTION',
            'item_urn_of_shadows': 'SUSTAINED_AOE',
            'item_spirit_vessel': 'SUSTAINED_AOE',
            'item_mage_slayer': 'SUSTAINED_AOE',
            'item_shivas_guard': 'MULTI_TARGET_ATTACK ANTI_HEAL',
            'item_maelstrom': 'LEAP', 'item_mjollnir': 'LEAP',
            'item_witch_blade': 'TRUE_STRIKE', 'item_devastator': 'TRUE_STRIKE',
        }
        for name, codes in rejected.items():
            with self.subTest(entity=name):
                self.assertFalse(set(codes.split()) & {f['mechanic'] for f in self.facts(name)})

    def test_upgrades_and_night_are_conditional(self):
        for name, code in (('doom_bringer_doom', 'BREAK'), ('naga_siren_ensnare', 'BREAK'),
                           ('weaver_time_lapse', 'ALLY_SAVE')):
            for fact in self.facts(name, code):
                self.assertTrue(any(c['kind'] == 'has-item' and c['code'] == 'item_ultimate_scepter'
                                    for c in json.loads(fact['requirements'])))
        for fact in self.facts('night_stalker_void', 'STUN'):
            self.assertIn('night', {c['code'] for c in json.loads(fact['requirements'])})
        self.assertTrue(self.facts('night_stalker_void', 'STUN'))


    def test_self_penalties_are_not_assigned_to_enemies(self):
        for name, code in (('item_mask_of_madness', 'ARMOR_REDUCTION'),
                           ('item_mask_of_madness', 'SILENCE'),
                           ('item_metamorphic_mandible', 'ARMOR_REDUCTION')):
            facts = self.facts(name, code)
            self.assertTrue(facts)
            self.assertEqual({f['target_scope'] for f in facts}, {'self'})

    def test_active_defense_and_stances_keep_their_activation_conditions(self):
        for name, code, condition in (
            ('brewmaster_drunken_brawler', 'EVASION', 'storm-stance'),
            ('antimage_counterspell', 'SPELL_REFLECTION', 'counterspell-active'),
            ('sandking_sand_storm', 'INVISIBILITY', 'inside-sandstorm'),
            ('bristleback_bristleback', 'DIRECTIONAL_DEFENSE', 'rear-or-side'),
        ):
            facts = self.facts(name, code)
            self.assertTrue(facts)
            for fact in facts:
                self.assertIn(condition, {c['code'] for c in json.loads(fact['requirements'])})

    def test_stale_evidence_unknown_entity_duplicate_and_bad_scope_are_rejected(self):
        for field, value in (('evidence', 'invented text'), ('entity_id', -1),
                             ('target_scope', 'unknown'), ('mechanic', 'UNKNOWN')):
            rows = self.subset('rubick_spell_steal', 'ABILITY_STEAL')
            rows['gameplay_mechanics'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                checked_mechanics(rows)
        rows = self.subset('rubick_spell_steal', 'ABILITY_STEAL')
        rows['gameplay_mechanics'] *= 2
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            checked_mechanics(rows)

    def test_removed_upgrade_condition_stops_export(self):
        rows = self.subset('doom_bringer_doom', 'BREAK')
        rows['gameplay_mechanics'][0]['requirements'] = '[]'
        with self.assertRaisesRegex(ValueError, 'Upgrade'):
            checked_mechanics(rows)

    def test_graph_retains_owner_target_explanation_and_item_condition(self):
        rows = self.subset('doom_bringer_doom', 'BREAK')
        game = schema_graph()
        for kind, source in (('ability', self.abilities['doom_bringer_doom']),
                             ('item', self.items['item_ultimate_scepter'])):
            owner = entity(kind, source[kind + '_id'])
            game.add((owner, RDF.type, KG.Ability if kind == 'ability' else KG.Item))
            game.add((owner, RDFS.label, literal(source['display_name'])))
        game += build_interactions(game, rows)
        owner = entity('ability', self.abilities['doom_bringer_doom']['ability_id'])
        effect = game.value(owner, KG.hasEffect)
        self.assertEqual(game.value(effect, KG.effectTarget), literal('enemy'))
        condition = game.value(effect, KG.requiresCondition)
        self.assertEqual(game.value(condition, KG.conditionItem), entity('item', self.items['item_ultimate_scepter']['item_id']))
        self.assertTrue(game.value(effect, RDFS.comment))
        validate_interactions(game, rows)
        game.set((effect, KG.effectTarget, literal('ally')))
        with self.assertRaisesRegex(ValueError, 'Interaction model'):
            validate_interactions(game, rows)
        game.set((effect, KG.effectTarget, literal('enemy')))
        game.set((RES['mechanic/BREAK'], RDFS.label, literal('invented definition')))
        with self.assertRaisesRegex(ValueError, 'Interaction model'):
            validate_interactions(game, rows)

    def test_refined_control_keeps_structured_dispel_requirement(self):
        rows = self.subset('lion_impale', 'STUN')
        game = schema_graph()
        owner = entity('ability', self.abilities['lion_impale']['ability_id'])
        game.add((owner, RDF.type, KG.Ability))
        game.add((owner, RDFS.label, literal('Earth Spike')))
        game.add((owner, KG.dispellableCode, literal(1)))
        legacy = Graph()
        coarse = RES['effect/coarse-stun']
        legacy.add((owner, KG.hasEffect, coarse))
        legacy.add((coarse, KG.effectMechanic, RES['mechanic/STUN']))
        legacy.add((coarse, KG.requiredDispel, literal('Strong')))
        condition = RES['condition/positioning']
        legacy.add((coarse, KG.requiresCondition, condition))
        legacy.add((condition, KG.conditionKind, literal('gameplay')))
        detail = build_gameplay_mechanics(game, rows, legacy=legacy)
        effect = detail.value(owner, KG.hasEffect)
        self.assertEqual(detail.value(effect, KG.requiredDispel), literal('Strong'))
        self.assertIn((effect, KG.requiresCondition, condition), detail)
