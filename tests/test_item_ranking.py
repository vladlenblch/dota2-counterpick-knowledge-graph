"""Purchase suitability, contextual mechanics and independence from hero ranking."""
import unittest

from rdflib import Graph, RDF, RDFS, URIRef

from dota_kg.ontology import literal
from dota_kg.ontology_schema import KG, RES
from dota_kg.item_ranking import ItemRanking
from dota_kg.ranking import RankingModel


class ItemRankingTests(unittest.TestCase):
    def setUp(self):
        self.g = Graph()
        self.hero = self.node('hero/owner', KG.Hero)
        self.enemy = self.node('hero/enemy', KG.Hero)
        self.other = self.node('hero/peer', KG.Hero)
        self.spell = self.node('ability/enemy', KG.Ability)
        self.g.add((self.enemy, KG.hasAbility, self.spell))
        for h in (self.hero, self.other):
            self.g.add((h, KG.mostPopularPosition, literal(1)))
            self.g.add((h, KG.attackType, literal('Melee')))
        self.effect(self.spell, 'TEST_DEFENSE', 'enemy')
        self.first = self.item('first', 'TEST_ONE')
        self.second = self.item('second', 'TEST_TWO')
        self.rule('TEST_ONE', 'TEST_DEFENSE', 'one', 2.)
        self.rule('TEST_TWO', 'TEST_DEFENSE', 'two', 2.)

    def node(self, path, cls):
        node = RES[path]
        self.g.add((node, RDF.type, cls))
        self.g.add((node, RDFS.label, literal(path)))
        return node

    def item(self, name, code, evidence='Active effect'):
        node = self.node('item/' + name, KG.Item)
        self.g.add((node, KG.isPurchasable, literal(True)))
        self.g.add((node, KG.cost, literal(2000)))
        self.effect(node, code, 'enemy', evidence)
        return node

    def effect(self, owner, code, scope, evidence='Effect evidence'):
        node = URIRef(str(owner) + '/effect/' + code)
        for s,p,o in ((owner, KG.hasEffect, node), (node, RDF.type, KG.Effect),
                      (node, KG.effectMechanic, RES['mechanic/' + code]),
                      (node, KG.effectTarget, literal(scope)), (node, KG.evidence, literal(evidence))):
            self.g.add((s,p,o))
        return node

    def rule(self, source, target, family, weight, purpose='counter', scope='enemy'):
        node = self.node('rule/' + source + '/' + target, KG.CounterRule)
        for p,v in ((KG.sourceMechanic, RES['mechanic/' + source]),
                    (KG.targetMechanic, RES['mechanic/' + target]),
                    (KG.ruleFamily, literal(family)), (KG.ruleWeight, literal(weight)),
                    (KG.rulePurpose, literal(purpose)), (KG.sourceEffectTarget, literal(scope))):
            self.g.add((node,p,v))
        return node

    def usage(self, hero, item, phase, count):
        node = URIRef(str(hero) + '/usage/' + str(item).rsplit('/',1)[-1] + '/' + phase)
        self.g.add((node, RDF.type, KG.ItemUsageObservation))
        for p,v in ((KG.hero, hero), (KG.item, item), (KG.phase, literal(phase)), (KG.usageCount, literal(count))):
            self.g.set((node,p,v))

    def parameter(self, owner, name, value):
        node = URIRef(str(owner) + '/param/' + name)
        val = URIRef(str(node) + '/value')
        for s,p,o in ((owner, KG.hasParameter, node), (node, KG.parameterName, literal(name)),
                      (node, KG.hasBaseValue, val), (val, KG.position, literal(0)),
                      (val, KG.numericValue, literal(value))):
            self.g.add((s,p,o))

    def ranking(self, **kwargs):
        return ItemRanking(RankingModel(self.g, **kwargs))

    def test_hero_specific_purchases_change_order_but_popularity_never_creates_counterplay(self):
        self.usage(self.hero,self.first,'mid',100)
        self.usage(self.hero,self.second,'mid',10)
        self.assertEqual(self.ranking().rank(self.hero,self.enemy)[0].item,self.first)
        self.usage(self.hero,self.second,'mid',200)
        self.assertEqual(self.ranking().rank(self.hero,self.enemy)[0].item,self.second)
        self.g.remove((self.second,KG.hasEffect,None))
        self.assertEqual([r.item for r in self.ranking().rank(self.hero,self.enemy)],[self.first])

    def test_timing_uses_actual_purchase_phases_and_not_only_six_old_item_hints(self):
        self.usage(self.hero,self.first,'early',100)
        self.usage(self.hero,self.second,'late',100)
        ranker=self.ranking()
        self.assertEqual(ranker.rank(self.hero,self.enemy,phase='early')[0].item,self.first)
        self.assertEqual(ranker.rank(self.hero,self.enemy,phase='late')[0].item,self.second)
        self.assertEqual(ranker.rank(None,self.enemy,phase='early')[0].item,self.first)
        with self.assertRaises(ValueError): ranker.rank(self.hero,self.enemy,phase='unknown')

    def test_rare_generic_items_are_excluded_but_strong_role_appropriate_counters_can_pass(self):
        self.usage(self.hero,self.first,'mid',100)
        self.usage(self.other,self.second,'mid',100)
        self.assertEqual([r.item for r in self.ranking().rank(self.hero,self.enemy)],[self.first])
        rule=RES['rule/TEST_TWO/TEST_DEFENSE']
        self.g.set((rule,KG.ruleFamily,literal('healing')))
        self.g.set((self.hero,KG.carryLevel,literal(3)))
        self.assertIn(self.second,[r.item for r in self.ranking().rank(self.hero,self.enemy)])

    def test_nonpurchasable_neutral_free_and_already_owned_items_are_not_new_purchases(self):
        for prop,value in ((KG.isPurchasable,False),(KG.isNeutral,True),(KG.cost,0)):
            previous=list(self.g.objects(self.first,prop))
            self.g.set((self.first,prop,literal(value)))
            self.assertNotIn(self.first,[r.item for r in self.ranking().rank(self.hero,self.enemy)])
            self.g.remove((self.first,prop,None))
            for v in previous:self.g.add((self.first,prop,v))
        self.assertNotIn(self.first,[r.item for r in self.ranking(own_items=(self.first,)).rank(self.hero,self.enemy)])

    def test_random_bash_is_not_a_guaranteed_disable_and_damage_is_not_a_proc_probability(self):
        self.g.remove((self.first,KG.hasEffect,None));self.g.remove((self.second,KG.hasEffect,None))
        self.effect(self.first,'STUN','enemy','Passive: chance to stun')
        self.effect(self.second,'STUN','enemy','Active: stun a target')
        self.parameter(self.first,'bash_chance_melee',25)
        self.parameter(self.first,'bonus_chance_damage',100)
        self.rule('STUN','TEST_DEFENSE','control',2.)
        rows=self.ranking().rank(self.hero,self.enemy)
        self.assertEqual(rows[0].item,self.second)
        self.assertAlmostEqual(rows[0].mechanical,2.)
        # An explicit named comparison retains the weaker alternative for inspection.
        weaker=self.ranking().rank(self.hero,self.enemy,owner=self.first)[0]
        self.assertAlmostEqual(weaker.mechanical,.5)

    def test_immunity_and_dispel_require_hostile_nonpiercing_or_dispellable_effects(self):
        self.g.remove((self.spell,KG.hasEffect,None))
        target=self.effect(self.spell,'SILENCE','self')
        immunity=self.item('immunity','DEBUFF_IMMUNITY')
        self.g.set((URIRef(str(immunity)+'/effect/DEBUFF_IMMUNITY'),KG.effectTarget,literal('self')))
        self.rule('DEBUFF_IMMUNITY','SILENCE','debuff-defense',2.,'purchase','self')
        self.assertEqual(self.ranking().rank(self.hero,self.enemy,owner=immunity),[])
        self.g.set((target,KG.effectTarget,literal('enemy')))
        self.assertTrue(self.ranking().rank(self.hero,self.enemy,owner=immunity))
        self.g.set((self.spell,KG.piercesDebuffImmunity,literal(True)))
        self.assertEqual(self.ranking().rank(self.hero,self.enemy,owner=immunity),[])
        cleanser=self.item('dispel','BASIC_DISPEL')
        self.g.set((URIRef(str(cleanser)+'/effect/BASIC_DISPEL'),KG.effectTarget,literal('self')))
        self.rule('BASIC_DISPEL','SILENCE','dispel-defense',2.,'purchase','self')
        self.assertEqual(self.ranking().rank(self.hero,self.enemy,owner=cleanser),[])
        self.g.set((self.spell,KG.dispellableCode,literal(2)))
        self.assertTrue(self.ranking().rank(self.hero,self.enemy,owner=cleanser))

    def test_repeated_facts_and_identical_main_actions_do_not_fill_the_answer(self):
        self.g.remove((self.second,KG.hasEffect,None));self.effect(self.second,'TEST_ONE','enemy')
        rows=self.ranking().rank(self.hero,self.enemy)
        self.assertEqual(len(rows),1)
        self.assertAlmostEqual(rows[0].mechanical,2.)
        self.assertEqual(len(self.ranking().rank(self.hero,self.enemy,owner=self.second)),1)

    def test_role_peers_with_another_attack_type_do_not_justify_a_rare_purchase(self):
        self.usage(self.hero,self.first,'mid',100)
        self.usage(self.other,self.second,'mid',100)
        self.g.set((RES['rule/TEST_TWO/TEST_DEFENSE'],KG.ruleFamily,literal('healing')))
        self.g.set((self.hero,KG.carryLevel,literal(3)))
        self.g.set((self.other,KG.attackType,literal('Ranged')))
        self.assertNotIn(self.second,[r.item for r in self.ranking().rank(self.hero,self.enemy)])
        self.g.set((self.other,KG.attackType,literal('Melee')))
        self.assertIn(self.second,[r.item for r in self.ranking().rank(self.hero,self.enemy)])

    def test_existing_native_cleave_reduces_value_of_another_cleave_purchase(self):
        self.g.remove((self.first,KG.hasEffect,None));self.effect(self.first,'CLEAVE_DAMAGE','enemy')
        self.rule('CLEAVE_DAMAGE','TEST_DEFENSE','units',4.)
        base=self.ranking().rank(self.hero,self.enemy,owner=self.first)[0].mechanical
        native=self.node('ability/cleave',KG.Ability)
        self.g.add((self.hero,KG.hasAbility,native));self.g.add((native,KG.behavior,literal(2)))
        self.effect(native,'CLEAVE_DAMAGE','enemy')
        self.assertLess(self.ranking().rank(self.hero,self.enemy,owner=self.first)[0].mechanical,base)

    def test_one_missproof_attack_per_cooldown_is_not_permanent_accuracy(self):
        self.g.remove((self.first,KG.hasEffect,None))
        effect=self.effect(self.first,'ACCURACY_PROC','enemy')
        condition=URIRef(str(effect)+'/cooldown-attack')
        self.g.add((effect,KG.requiresCondition,condition))
        self.parameter(self.first,'cooldown',10.)
        self.g.add((self.first,KG.hasCooldown,URIRef(str(self.first)+'/param/cooldown')))
        self.rule('ACCURACY_PROC','TEST_DEFENSE','accuracy',2.)
        row=self.ranking().rank(self.hero,self.enemy,owner=self.first)[0]
        self.assertAlmostEqual(row.mechanical,2. * 1.7 / 10.)

    def test_purchase_only_rules_do_not_change_hero_scores(self):
        ability=self.node('ability/own',KG.Ability)
        self.g.add((self.hero,KG.hasAbility,ability));self.effect(ability,'TEST_ONE','enemy')
        before=RankingModel(self.g).assess(self.hero,self.enemy).score
        self.effect(ability,'PURCHASE_ONLY','enemy')
        self.rule('PURCHASE_ONLY','TEST_DEFENSE','one',200.,'purchase')
        after=RankingModel(self.g).assess(self.hero,self.enemy)
        self.assertEqual(after.score,before)
        self.assertEqual(before,2.)


if __name__=='__main__':unittest.main()
