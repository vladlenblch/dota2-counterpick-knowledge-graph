"""Rank purchases from RDF counterplay, observed builds and purchase timing."""
from collections import defaultdict
from dataclasses import dataclass, replace
import math

from rdflib import RDF, URIRef

from .ontology_schema import KG

# Rare situational counters may pass through role evidence; generic farming
# alternatives must have evidence of use by the actual hero.
SITUATIONAL_FAMILIES = {'passive', 'healing', 'accuracy', 'control', 'attack-denial', 'spell-defense', 'mana'}


@dataclass
class ItemAssessment:
    item: URIRef
    score: float
    mechanical: float
    suitability: float
    timing: float
    usage: int
    groups: list

    @property
    def primary(self):
        return self.groups[0]['applications'][0]


class ItemRanking:
    def __init__(self, model):
        self.model, self.graph = model, model.graph
        self.usage = defaultdict(lambda: defaultdict(dict))
        self.population = defaultdict(lambda: defaultdict(int))
        self.cohorts = defaultdict(lambda: defaultdict(int))
        self.maximum = {}
        for node in self.graph.subjects(RDF.type, KG.ItemUsageObservation):
            hero, item = self.graph.value(node, KG.hero), self.graph.value(node, KG.item)
            phase = model.value(node, KG.phase)
            count = model.value(node, KG.usageCount, 0)
            if phase == 'start' or not self.purchasable(item) or not isinstance(count, int) or count <= 0:
                continue
            self.usage[hero][item][phase] = self.usage[hero][item].get(phase, 0) + count
            self.population[item][phase] += count
            for pos in {model.value(hero, KG.mostPopularPosition), model.value(hero, KG.secondMostPopularPosition)} - {None}:
                attack = model.value(hero, KG.attackType)
                for key in {(pos, attack), (pos, None)}:
                    self.cohorts[key][item] += count
        for hero, items in self.usage.items():
            self.maximum[hero] = max(sum(phases.values()) for phases in items.values())
        self.cohort_max = {pos: max(items.values(), default=1) for pos, items in self.cohorts.items()}

    def purchasable(self, item):
        m = self.model
        return (item in m.items and m.value(item, KG.isPurchasable) is True
                and m.value(item, KG.isNeutral) is not True and m.value(item, KG.cost, 1) > 0)

    def fit(self, hero, item, *, position=None, phase=None):
        m = self.model
        purchases = self.usage.get(hero, {})
        observed = purchases.get(item, {})
        count = sum(observed.values())
        timing = self.purchase_timing(item, phase)
        if not purchases:
            return 1., timing, count, 0.
        if position is None:
            position = m.value(hero, KG.mostPopularPosition)
        cohort_key = position, m.value(hero, KG.attackType)
        cohort = self.cohorts.get(cohort_key, {}).get(item, 0) / self.cohort_max.get(cohort_key, 1)
        if phase:
            def n(phases):
                return phases.get(phase, 0) + .2 * sum(phases.values())
            relative = n(observed) / max(n(values) for values in purchases.values())
        else:
            relative = count / self.maximum[hero]
        fit = .15 + .7 * math.sqrt(relative) + .15 * math.sqrt(cohort)
        return fit, timing, count, cohort

    def purchase_timing(self, item, phase):
        if phase is None:
            return 1.
        population = self.population.get(item, {})
        if population:
            if phase == 'early' and not population.get(phase):
                return 0.
            return .1 + .9 * math.sqrt(population.get(phase, 0) / max(population.values()))
        phases = {self.model.value(p, KG.purchasePhase) for p in self.graph.subjects(KG.item, item)
                  if (p, RDF.type, KG.ItemRolePreference) in self.graph}
        return 0. if phases and phase not in phases else 1.

    def applications(self, hero, enemy, *, owner=None, purpose='counter', focus=None, needs=None):
        m = self.model
        apps = list(m.applications(hero, enemy, items=True, owner=owner, purpose=purpose, focus=focus, needs=needs, deduplicate=False))
        if purpose == 'counter':
            apps += m.applications(hero, enemy, items=True, owner=owner, purpose='purchase', focus=focus, needs=needs, deduplicate=False)
        native = m.applications(hero, enemy, focus=focus) if hero in m.heroes else ()
        native_strength = defaultdict(float)
        for app in native:
            native_strength[app.fact.code] = max(native_strength[app.fact.code], app.value)
        target_codes = m.profile(enemy, m.enemy_items)[1]
        result = []
        for app in apps:
            item, code = app.fact.owner, app.fact.code
            if not self.purchasable(item):
                continue
            # A required ranged attack must not become a melee item's benefit.
            if (any(str(c).endswith('/ranged-attacks') for c in app.fact.conditions)
                    and hero in m.stats and m.stats[hero]['attackType'] != 'Ranged'):
                continue
            if (any(str(c).endswith('/enemy-unit-target-spell') for c in app.fact.conditions)
                    and hero in m.stats and not any(int(m.value(a, KG.behavior, 0)) & 8
                    and int(m.value(a, KG.targetTeam, 0)) & 2
                    for a in m.profile(hero, m.own_items)[2] if a in m.abilities.get(hero, ()))):
                continue
            witnesses = app.need.facts
            if code == 'DEBUFF_IMMUNITY':
                witnesses = tuple(f for f in witnesses if f.recipient in {'enemy', 'area', 'both'}
                                  and m.value(f.owner, KG.piercesDebuffImmunity) is not True)
            elif code == 'BASIC_DISPEL' and app.rule.purpose == 'purchase':
                witnesses = tuple(f for f in witnesses if f.recipient in {'enemy', 'area', 'both'}
                                  and (m.value(f.uri, KG.requiredDispel) == 'Basic'
                                  or m.value(f.owner, KG.dispellableCode) == 2))
            if code == 'SPELL_BLOCK' and app.rule.target == 'FORCED_COMBAT':
                witnesses = tuple(f for f in witnesses if int(m.value(f.owner, KG.behavior, 0)) & 8)
            if code == 'SPELL_REFLECTION':
                forced = {f.owner for f in m.profile(enemy, m.enemy_items)[1].get('FORCED_COMBAT', ())}
                witnesses = tuple(f for f in witnesses if f.owner not in forced)
            if app.need.facts and not witnesses:
                continue
            if code == 'INVISIBILITY' and 'DETECTION' in target_codes:
                continue
            if purpose == 'counter' and app.rule.family == 'sustain':
                continue  # Generic regeneration is not a specific counter-purchase.
            value = app.value
            if code == 'STUN' and 'chance' in app.fact.evidence.lower():
                chances = [v for p, h, v in m.parameter_values(item) if 'chance' in p['name'].lower() and 'damage' not in p['name'].lower() and 0 < v <= 100
                           and ('ranged' if m.stats.get(hero, {}).get('attackType') == 'Melee' else 'melee') not in p['name'].lower()]
                if chances:
                    value *= max(chances) / 100
            if code == 'TRUE_STRIKE' and any(str(c).endswith('/active') for c in app.fact.conditions):
                durations = [v for p, h, v in m.parameter_values(item) if p['name'] in {'duration', 'silence_duration'} and v > 0]
                cooldown = m.native.get(item, {}).get('cooldown', 0)
                if durations and cooldown > 0:
                    value *= min(1., max(durations) / cooldown)
            if code == 'ACCURACY_PROC':
                chances = [v for p, h, v in m.parameter_values(item) if 'chance' in p['name'].lower()
                           and 'damage' not in p['name'].lower() and 0 < v <= 100]
                if chances:
                    value *= max(chances) / 100
            if code == 'ACCURACY_PROC' and any(str(c).endswith('/cooldown-attack') for c in app.fact.conditions):
                cooldown = m.native.get(item, {}).get('cooldown', 0)
                if cooldown > 0:
                    value *= min(1., m.value(hero, KG.attackTime, 1.7) / cooldown)
            if code in {'ACCURACY_PROC', 'TRUE_STRIKE'}:
                evasion = [v for fact in witnesses for p, h, v in m.parameter_values(fact.owner)
                           if 'evasion' in p['name'].lower() and 0 < v < 100]
                if evasion:
                    value *= 1 / (1 - max(evasion) / 100)
            if code == 'ALLY_REPOSITION' and m.stats.get(hero, {}).get('attackType') == 'Ranged':
                ranges = [v for p, h, v in m.parameter_values(item) if p['name'] == 'base_attack_range' and v > 0]
                if ranges:
                    value *= 1 + min(.3, max(ranges) / 600)

            if 'FORCED_COMBAT' in target_codes and code in {'ALLY_REPOSITION', 'FORCED_MOVEMENT', 'INVISIBILITY'}:
                value *= .15  # Escape cannot be activated after forced combat starts.
            # Killing one illusion is not equivalent to clearing a reproducing army.
            if code == 'ANTI_ILLUSION' and app.rule.family == 'units':
                value *= .15
            # Buying another copy of an already strong native cleave is poor value.
            if code in {'CLEAVE_DAMAGE', 'CHAIN_LIGHTNING', 'SUSTAINED_AOE', 'AOE_DAMAGE', 'MANA_BURN'}:
                value *= max(.2, 1 - .8 * min(1., native_strength[code] / max(value, .001)))
            # Numeric bonuses distinguish a small armor component from a finished item.
            if code == 'ARMOR_BUFF':
                armor = [v for p, h, v in m.parameter_values(item)
                         if 'armor' in p['name'].lower() and 'reduction' not in p['name'].lower() and v > 0]
                if armor:
                    value *= max(.35, min(1.8, math.sqrt(max(armor) / 10)))
            if code == 'ANTI_HEAL':
                reductions = [v for p, h, v in m.parameter_values(item) if v > 0 and
                              any(w in p['name'].lower() for w in ('restoration_reduction', 'heal_reduction'))]
                if reductions:
                    value *= max(.4, min(1.4, max(reductions) / 40))
            result.append(replace(app, need=replace(app.need, facts=witnesses), value=value))
        hits = {}
        for app in result:
            key = app.fact.owner, app.rule.family
            order = lambda a: (-a.value, len(a.fact.conditions), a.fact.code, a.rule.target, str(a.fact.uri))
            if key not in hits or order(app) < order(hits[key]):
                hits[key] = app
        return tuple(sorted(hits.values(), key=lambda a: (-a.value, str(a.fact.owner), a.rule.family)))

    def rank(self, hero, enemy, *, position=None, phase=None, owner=None, purpose='counter', focus=None, needs=None):
        if phase not in {None, 'early', 'mid', 'late'}:
            raise ValueError('Стадия покупки должна быть early, mid или late.')
        if position is not None and position not in range(1, 6):
            raise ValueError('Позиция должна быть от 1 до 5.')
        by_item = defaultdict(list)
        for app in self.applications(hero, enemy, owner=owner, purpose=purpose, focus=focus, needs=needs):
            if app.fact.owner in self.model.own_items and owner is None:
                continue
            by_item[app.fact.owner].append(app)
        results = []
        for item, apps in by_item.items():
            fit, timing, count, cohort = self.fit(hero, item, position=position, phase=phase)
            maximum = self.maximum.get(hero, 0)
            # Cheap items described only by numeric bonuses are components rather
            # than a default finished purchase; early defense remains available.
            if (owner is None and purpose == 'counter' and phase != 'early'
                    and self.model.value(item, KG.cost, 2000) < 2000
                    and all(f.evidence.startswith('{') for f in self.model.owner_facts.get(item, ()))):
                continue
            if (owner is None and maximum >= 20 and count / maximum < .025
                    and not (purpose == 'compensate' and count > 0)):
                # Broadly useful, role-compatible situational answers can survive a
                # sparse personal sample. Unobserved generic damage items cannot.
                carry = (self.model.value(hero, KG.mostPopularPosition) == 1
                         and self.model.value(hero, KG.carryLevel, 0) >= 2)
                if not (cohort >= .02 and any((a.rule.family in SITUATIONAL_FAMILIES or a.rule.target == 'FORCED_COMBAT')
                        and a.need.importance >= .7 and a.value >= 1.5
                        and (count > 0 or carry or a.rule.target == 'FORCED_COMBAT') for a in apps)):
                    continue
            total, groups = self.model.aggregate(apps)
            score = total * fit * timing
            if score > 0:
                results.append(ItemAssessment(item, score, total, fit, timing, count, groups))
        ordered = sorted(results, key=lambda row: (-row.score, str(row.item)))
        if owner is not None:
            return ordered
        # Offer different solutions, not several purchases with the same main action.
        selected, seen = [], set()
        for row in ordered:
            key = row.primary.rule.family, (None if row.primary.rule.family == 'accuracy' else row.primary.fact.code)
            if key not in seen:
                selected.append(row)
                seen.add(key)
        return selected
