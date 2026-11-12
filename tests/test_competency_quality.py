"""Semantic regressions found by auditing diverse real competency questions."""
import re
from pathlib import Path
import unittest

from rdflib import OWL, RDF, URIRef

from dota_kg.answers import KnowledgeBase
from dota_kg.ontology import load_ontology
from dota_kg.ontology_schema import KG

ROOT = Path(__file__).resolve().parents[1]


class CompetencyQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_ontology(ROOT/'ontology',cache=True)
        cls.kb = KnowledgeBase(cls.graph)

    def ask(self,text,**kwargs):
        return self.kb.ask(text,**kwargs)

    def test_upgraded_effect_conditions_do_not_become_base_ability_requirements(self):
        for name,upgrade in (('Storm Hammer','Scepter'),('Black Hole','Scepter'),('Fissure','Shard')):
            with self.subTest(ability=name):
                result=self.ask('при каких условиях работает '+name,limit=20)
                self.assertTrue(result['answers'])
                base=[r for r in result['answers'] if not r.get('conditional')]
                self.assertTrue(base)
                self.assertTrue(all(upgrade not in r['answer'] or 'не блокируется' in r['answer'] for r in base))
                self.assertTrue(any(r.get('conditional') and upgrade in r['answer'] for r in result['answers']))

    def test_named_hero_disambiguates_duplicate_spell_labels(self):
        parsed=self.kb.parser.parse('при каких условиях работает Lion Hex')
        self.assertEqual(parsed.cq,3)
        self.assertIn(parsed.subject,self.graph.objects(parsed.hero,KG.hasAbility))
        with self.assertRaisesRegex(ValueError,'Lion'):
            self.kb.parser.parse('при каких условиях работает Hex')

    def test_detection_returns_basic_tools_and_real_riki_invisibility(self):
        for question in ('чем обнаруживать невидимых противников','чем обнаружить Riki'):
            with self.subTest(question=question):
                result=self.ask(question,limit=3)
                items=[r for r in result['answers'] if r.get('kind')=='item']
                names={self.kb.label(URIRef(r['source'])) for r in items}
                self.assertEqual(names,{'Gem of True Sight','Sentry Ward','Dust of Appearance'})
                if 'Riki' in question:
                    self.assertTrue(all(r.get('target_facts') for r in items))

    def test_smoke_and_shadow_dance_are_not_revealed_by_true_sight(self):
        for question in ('чем обнаружить Smoke of Deceit','чем обнаружить Slark'):
            with self.subTest(question=question):
                result=self.ask(question)
                self.assertTrue(result['answers'])
                self.assertTrue(all(r.get('counterable') is False for r in result['answers']))
                self.assertTrue(all('не раскрывается' in r['answer'] for r in result['answers']))

    def test_dispel_uses_official_requirements_for_previously_unhandled_debuffs(self):
        for hero in ('Silencer','Venomancer','Slardar','Treant Protector'):
            with self.subTest(hero=hero):
                result=self.ask('какие эффекты '+hero+' можно снять развеиванием',limit=20)
                self.assertTrue(result['answers'])
                removable=[r for r in result['answers'] if r['required']=='Basic' and not r.get('offensive')]
                self.assertTrue(removable)
                self.assertTrue(any(not r.get('strong') for r in removable))
                self.assertTrue(any(r.get('kind')=='item' for r in removable))

    def test_doom_is_not_basic_dispellable_just_because_it_casts_a_basic_dispel(self):
        result=self.ask('можно ли снять Doom развеиванием')
        self.assertTrue(result['answers'])
        self.assertTrue(all(r['required']=='None' for r in result['answers']))
        self.assertTrue(all('не снимается' in r['answer'] for r in result['answers']))

    def test_enemy_buffs_need_offensive_purge(self):
        result=self.ask('можно ли снять Ghost Shroud развеиванием',limit=20)
        offensive=[r for r in result['answers'] if r.get('offensive') and r.get('cleanser')]
        self.assertTrue(offensive)
        self.assertTrue(all(r['scope'] in {'enemy','both'} for r in offensive))
        self.assertTrue(any('Nullifier' in r['answer'] for r in offensive))
        purchase=self.ask('что купить Phantom Assassin против Necrophos')
        self.assertIn('Nullifier',purchase['answers'][0]['answer'])

    def test_granted_piercing_ability_keeps_its_required_upgrade(self):
        result=self.ask('какие способности Phantom Assassin проходят сквозь BKB')
        rows=[r for r in result['answers'] if 'Fan of Knives' in r['answer']]
        self.assertTrue(rows)
        self.assertTrue(all('Scepter' in r['answer'] for r in rows))
        self.assertTrue(all(r['pierces'] for r in rows))

    def test_bkb_evaluation_covers_rules_beyond_old_channel_interrupts(self):
        result=self.ask('как Rubick контрит Phantom Lancer если у противника активный BKB')
        text=' '.join(r['answer'] for r in result['answers'])
        self.assertIn('Telekinesis',text)
        self.assertIn('контроль блокируется',text)
        self.assertIn('Fade Bolt',text)
        self.assertIn('урон уменьшается',text)

    def test_spell_block_and_reflection_do_not_mean_the_same_as_immunity(self):
        blocked=self.ask("как Lion контрит Anti-Mage если у противника Linken's Sphere")
        text={self.kb.label(URIRef(r['source'])):r['answer'] for r in blocked['answers']}
        self.assertIn('Готовый блок',text['Hex'])
        self.assertIn('не срабатывает',text['Earth Spike'])
        self.assertNotIn('активирован',text['Hex'])
        reflected=self.ask('как Lion контрит Phantom Lancer если у противника активный Lotus Orb')
        text={self.kb.label(URIRef(r['source'])):r['answer'] for r in reflected['answers']}
        self.assertIn('исходный эффект',text['Hex'])
        self.assertIn('сохранится',text['Hex'])
        self.assertIn('не вызывает отражение',text['Earth Spike'])

    def test_upgrade_descriptions_use_upgrade_values_not_inactive_base_zeroes(self):
        for question in ('что аганим меняет у Enigma','что шард меняет у Lion','что аганим меняет у Anti-Mage'):
            with self.subTest(question=question):
                result=self.ask(question,limit=20)
                text=' '.join(r['answer'] for r in result['answers'])
                self.assertTrue(text)
                self.assertNotRegex(text,r'(?<![\d.])0%')
                self.assertNotRegex(text,r'%[a-zA-Z_]+%|\{s:')

    def test_talent_and_facet_text_is_meaningful_and_preserves_all_modifiers(self):
        result=self.ask('какие таланты у Windranger',limit=30)
        text=' '.join(r['answer'] for r in result['answers'])
        self.assertIn('16%',text)
        self.assertIn('0.75',text)
        self.assertNotIn('{s:',text)
        execute=[r for r in result['answers'] if '16%' in r['answer']][0]
        self.assertGreaterEqual(len(execute['modifiers']),2)
        result=self.ask('какие аспекты у Phantom Lancer',limit=30)
        text=' '.join(r['answer'] for r in result['answers'])
        self.assertIn('25%',text)
        self.assertIn("нельзя управлять",text)
        self.assertNotIn('phantom_lancer_',text)

    def test_slow_resistance_is_not_hostile_slow_and_ally_armor_is_not_self_cast(self):
        for name in ('Guardian Sprint','Surge','Shapeshift'):
            owner=self.kb.parser.resolve_entity(name,KG.Ability)
            facts=[f for f in self.kb.ranking.owner_facts[owner]
                   if self.kb.ranking.conditions_available(f,())]
            self.assertFalse(any(f.code=='SLOW' for f in facts),name)
            self.assertTrue(any(f.code in {'SLOW_RESISTANCE','SLOW_IMMUNITY'} for f in facts),name)
        sprint=self.ask('можно ли снять Guardian Sprint развеиванием')
        self.assertTrue(sprint['answers'])
        self.assertTrue(all(r.get('offensive') for r in sprint['answers']))
        crest=self.kb.parser.resolve_entity('Solar Crest',KG.Item)
        active=[f for f in self.kb.ranking.owner_facts[crest]
                if f.code=='ARMOR_BUFF' and f.evidence.startswith('Active:')]
        self.assertTrue(active)
        self.assertTrue(all(f.recipient=='ally' for f in active))
        purchase=self.ask('что купить Phantom Assassin против Bristleback',limit=10)
        self.assertFalse(any('Nullifier' in r['answer'] for r in purchase['answers']))

    def test_channel_interrupts_prefer_direct_control_and_explain_triggers(self):
        result=self.ask('чем прервать Black Hole',limit=20)
        abilities=[r for r in result['answers'] if r.get('kind')=='ability']
        self.assertTrue(abilities)
        self.assertNotIn('Shadow Walk',abilities[0]['answer'])
        self.assertNotIn('Spiked Carapace',abilities[0]['answer'])
        delayed=self.ask('может ли Silencer прервать Black Hole',limit=20)
        rows=[r for r in delayed['answers'] if 'Last Word' in r['answer']]
        self.assertTrue(rows)
        self.assertTrue(all('после завершения' in r['answer'] for r in rows))

    def test_early_armor_compensation_does_not_recommend_an_expensive_creep_control_item(self):
        result=self.ask('какие уязвимости Lion компенсировать предметами в начале игры')
        self.assertTrue(result['answers'])
        self.assertNotIn('Helm of the Dominator',result['answers'][0]['answer'])
        item=URIRef(result['answers'][0]['source'])
        self.assertLessEqual(self.kb.value(item,KG.cost),1000)

    def test_clean_schema_keeps_gameplay_and_removes_technical_layers(self):
        for name in ('HeroContext','ItemRolePreference','heldItem','selectedTalent','conditionTalent',
                     'contextComplete','ruleStatus','effectStatus','preferenceRank','purchasePhase','dispelTarget'):
            term=KG[name]
            self.assertFalse(list(self.graph.triples((term,None,None))))
            self.assertFalse(list(self.graph.triples((None,term,None))))
        self.assertEqual(len(set(self.graph.subjects(RDF.type,OWL.Class))),21)
        self.assertFalse(any(self.kb.value(c,KG.conditionKind)=='review' for c in self.graph.subjects(RDF.type,KG.InteractionCondition)))

    def test_wards_are_not_treated_as_ordinary_aoe_clearable_units(self):
        hero=self.kb.parser.resolve_entity('Shadow Shaman',KG.Hero)
        profile=self.kb.ranking.profile(hero)
        self.assertIn('SUMMONS_WARDS',profile[1])
        self.assertNotIn('GROUPED_UNITS',profile[3])


if __name__=='__main__':unittest.main()
