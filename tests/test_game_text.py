"""Tooltip rendering uses named RDF parameters, not row order or guessed arithmetic."""
from pathlib import Path
import unittest

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from dota_kg.game_text import render_game_text, render_russian_game_text, talent_text, facet_text
from dota_kg.ontology_schema import KG, RES


class GameTextTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.owner = RES['ability/example']

    def parameter(self, name, values, *, owner=None, heading=None, required_facet=None):
        owner = self.owner if owner is None else owner
        parameter = URIRef(f'{owner}/parameter/{name}')
        self.graph.add((owner, KG.hasParameter, parameter))
        self.graph.add((parameter, KG.parameterName, Literal(name)))
        if heading is not None:
            self.graph.add((parameter, KG.parameterHeading, Literal(heading)))
        if required_facet is not None:
            self.graph.add((parameter, KG.requiredFacet, Literal(required_facet)))
        self.series(parameter, values)
        return parameter

    def series(self, parent, values):
        for index, value in enumerate(values):
            node = URIRef(f'{parent}/value/{index}')
            self.graph.add((parent, KG.hasBaseValue, node))
            self.graph.add((node, KG.position, Literal(index)))
            self.graph.add((node, KG.numericValue, Literal(value)))

    def modifier(self, parameter, name, values, *, talent=None, operation=0):
        node = URIRef(f'{parameter}/modifier/{name}')
        self.graph.add((parameter, KG.hasModifier, node))
        self.graph.add((node, KG.operation, Literal(operation)))
        if talent is not None:
            self.graph.add((node, KG.requiresTalent, talent))
            self.graph.add((node, KG.modifierKind, Literal('bonus')))
            self.graph.add((node, KG.numericValue, Literal(values[0])))
        else:
            self.graph.add((node, KG.modifierKind, Literal('facet')))
            self.graph.add((node, KG.conditionName, Literal(name)))
            self.series(node, values)
        return node

    def note(self, text, index):
        note = URIRef(f'{self.owner}/facet-note/{index}')
        for s,p,o in ((self.owner, KG.hasNote, note), (note, KG.noteKind, Literal('facet')),
                      (note, KG.noteText, Literal(text))):
            self.graph.add((s,p,o))

    def test_single_facet_and_single_plain_note_are_unambiguous(self):
        parameter = self.parameter('damage',[0])
        self.modifier(parameter,'facet_one',[2])
        self.note('Illusions acquire nearby targets automatically.',0)
        self.assertEqual(facet_text(self.graph,self.owner,'facet_one'),
                         'Illusions acquire nearby targets automatically.')
        other = self.parameter('duration',[0])
        self.modifier(other,'facet_two',[8])
        self.assertNotEqual(facet_text(self.graph,self.owner,'facet_one'),
                            'Illusions acquire nearby targets automatically.')

    def test_base_vectors_zero_decimals_and_valve_percent_escaping(self):
        self.parameter('damage', [100, 200, 300])
        self.parameter('chance', [25])
        self.parameter('mana', [0])
        self.parameter('delay', [.75])
        text = '<b>Damage</b>: %damage%; chance %chance%%%; cost %mana%; delay %delay%s.'
        self.assertEqual(render_game_text(self.graph,self.owner,text),
                         'Damage: 100/200/300; chance 25%; cost 0; delay 0.75s.')

    def test_bonus_namespace_cannot_accidentally_resolve_to_base(self):
        self.parameter('damage', [400])
        text = '+{s:bonus_damage} damage'
        self.assertEqual(render_game_text(self.graph,self.owner,text),text)
        self.assertEqual(render_game_text(self.graph,self.owner,text,values={'damage':20}),'+20 damage')
        self.assertEqual(render_game_text(self.graph,self.owner,text,
                         values={'damage':20,'bonus_damage':99}),'+20 damage')

    def test_upgrade_bonus_aliases_use_only_explicit_upgrade_values(self):
        self.parameter('AbilityCooldown',[120])
        text='Reduces cooldown by %bonus_AbilityCooldown%.'
        self.assertEqual(render_game_text(self.graph,self.owner,text),text)
        self.assertEqual(render_game_text(self.graph,self.owner,text,values={'AbilityCooldown':40}),
                         'Reduces cooldown by 40.')
        self.assertEqual(render_game_text(self.graph,self.owner,'%bonus_damage%',
                         values={'bonus_damage':99,'damage':20}),'99')

    def test_missing_numbers_do_not_trigger_personal_paraphrases(self):
        text=("Every %tick_rate%s that enemy heroes stay under the effect of Natural Order, "
              "their armor is reduced by %armor_per_tick%, and magic resistance is reduced by "
              "%magic_resistance_per_tick%%%. Stacks up to %max_stacks% times.")
        result=render_russian_game_text(self.graph,self.owner,text)
        self.assertIn('%tick_rate%',result)
        self.assertNotIn('постепенно снижает броню',result)
        result=render_russian_game_text(self.graph,self.owner,text,
            values={'tick_rate':1,'armor_per_tick':2,'magic_resistance_per_tick':3,'max_stacks':4})
        self.assertIn('2',result)
        self.assertIn('3%',result)
        self.assertNotIn('постепенно снижает броню',result)

    def test_all_talent_parameters_are_matched_by_name_not_current_modifier(self):
        talent = RES['talent/execute']
        self.graph.add((talent,RDFS.label,Literal('Execute below {s:bonus_max_execute_threshold}%; minimum {s:bonus_min_execute_threshold}%.')))
        low = self.parameter('min_execute_threshold', [])
        high = self.parameter('max_execute_threshold', [])
        self.modifier(low,'low',[10],talent=talent)
        self.modifier(high,'high',[16],talent=talent)
        other = self.parameter('max_execute_threshold', [],owner=RES['ability/other'])
        self.modifier(other,'other',[99],talent=talent)
        self.assertEqual(talent_text(self.graph,self.owner,talent),'Execute below 16%; minimum 10%.')
        reversed_graph = Graph()
        for triple in reversed(list(self.graph)):
            reversed_graph.add(triple)
        self.assertEqual(talent_text(reversed_graph,self.owner,talent),talent_text(self.graph,self.owner,talent))

    def test_facet_overrides_do_not_add_operation_five_to_base(self):
        duration = self.parameter('duration',[20])
        attacks = self.parameter('attacks_per_second',[0,0,0])
        self.modifier(duration,'facet_alternative',[8],operation=5)
        self.modifier(attacks,'facet_alternative',[7,9,11],operation=0)
        self.note('Lasts %duration%s and attacks %attacks_per_second% times.',0)
        self.assertEqual(facet_text(self.graph,self.owner,'facet_alternative'),
                         'Lasts 8s and attacks 7/9/11 times.')
        self.assertEqual(render_game_text(self.graph,self.owner,'Lasts %duration%s.'),'Lasts 20s.')

    def test_notes_identify_the_facet_by_tokens_not_by_position(self):
        damage = self.parameter('damage',[100])
        radius = self.parameter('radius',[200])
        self.modifier(damage,'facet_damage',[30])
        self.modifier(radius,'facet_radius',[500])
        self.note('Radius becomes %radius%.',0)
        self.note('Damage bonus is %damage%.',1)
        self.assertEqual(facet_text(self.graph,self.owner,'facet_damage'),'Damage bonus is 30.')
        self.assertEqual(facet_text(self.graph,self.owner,'facet_radius'),'Radius becomes 500.')

    def test_no_token_notes_from_different_facets_are_not_guessed(self):
        damage = self.parameter('damage',[100],heading='DAMAGE:')
        self.modifier(damage,'facet_damage',[30])
        self.note('Unrelated immunity from one facet.',0)
        self.note('Different movement from another facet.',1)
        rendered = facet_text(self.graph,self.owner,'facet_damage')
        self.assertEqual(rendered,'Значения аспекта: УРОН: 30')
        self.assertNotIn('facet_damage',rendered)
        self.assertNotIn('immunity',rendered)
        self.assertNotIn('movement',rendered)

    def test_shared_parameter_token_cannot_arbitrarily_choose_between_facets(self):
        damage = self.parameter('damage',[100],heading='DAMAGE:')
        self.modifier(damage,'first',[10])
        self.modifier(damage,'second',[20])
        self.note('One unspecified facet changes %damage%.',0)
        self.assertEqual(facet_text(self.graph,self.owner,'first'),'Значения аспекта: УРОН: 10')
        self.assertEqual(facet_text(self.graph,self.owner,'second'),'Значения аспекта: УРОН: 20')

    def test_unselected_facet_parameter_is_not_used_as_a_base_value(self):
        self.parameter('duration',[8],required_facet='alternative')
        self.assertEqual(render_game_text(self.graph,self.owner,'Duration %duration%'),'Duration %duration%')
        self.assertEqual(facet_text(self.graph,self.owner,'alternative'),'Значения аспекта: duration: 8')

    def test_facet_modifier_takes_precedence_over_its_scoped_zero_stub(self):
        parameter = self.parameter('threshold',[0],required_facet='specific')
        self.modifier(parameter,'specific',[25])
        self.note('Creates an illusion every %threshold%%% missing health.',0)
        self.assertEqual(facet_text(self.graph,self.owner,'specific'),
                         'Creates an illusion every 25% missing health.')

    def test_unresolved_talent_label_falls_back_to_known_bonuses_without_invention(self):
        talent = RES['talent/unknown-token']
        self.graph.add((talent,RDFS.label,Literal('+{s:bonus_unknown} damage')))
        damage = self.parameter('damage',[100],heading='DAMAGE:')
        self.modifier(damage,'known',[30],talent=talent)
        self.assertEqual(talent_text(self.graph,self.owner,talent),'Бонусы таланта: УРОН: 30')
        self.assertEqual(facet_text(self.graph,self.owner,'unknown_facet'),'')


class SavedGameTextScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dota_kg.ontology import load_ontology
        cls.graph = load_ontology(Path(__file__).resolve().parents[1]/'ontology')

    def ability(self, name):
        matches = [node for node in self.graph.subjects(RDF.type,KG.Ability)
                   if str(self.graph.value(node,RDFS.label)) == name]
        self.assertEqual(len(matches),1)
        return matches[0]

    def test_windranger_talents_match_the_saved_numeric_modifiers(self):
        powershot = self.ability('Powershot')
        candidates = []
        for parameter in self.graph.objects(powershot,KG.hasParameter):
            for modifier in self.graph.objects(parameter,KG.hasModifier):
                talent = self.graph.value(modifier,KG.requiresTalent)
                if talent is not None:
                    rendered = talent_text(self.graph,powershot,talent)
                    if 'добива' in rendered.lower():
                        candidates.append(rendered)
        self.assertTrue(candidates)
        self.assertTrue(all('16' in text and '{s:' not in text for text in candidates))
        shackleshot = self.ability('Shackleshot')
        texts = [talent_text(self.graph,shackleshot,talent)
                 for p in self.graph.objects(shackleshot,KG.hasParameter)
                 for m in self.graph.objects(p,KG.hasModifier)
                 if (talent := self.graph.value(m,KG.requiresTalent)) is not None]
        self.assertTrue(any('0.75' in text for text in texts))

    def test_phantom_lancer_facets_show_values_and_never_internal_identifiers(self):
        convergence = facet_text(self.graph,self.ability('Doppelganger'),'phantom_lancer_convergence')
        divergence = facet_text(self.graph,self.ability('Juxtapose'),'phantom_lancer_divergence')
        self.assertIn('25%',convergence)
        self.assertNotIn('phantom_lancer_',convergence)
        self.assertTrue(divergence)
        self.assertNotIn('phantom_lancer_',divergence)
        self.assertIn("нельзя управлять",divergence)


if __name__ == '__main__':
    unittest.main()
