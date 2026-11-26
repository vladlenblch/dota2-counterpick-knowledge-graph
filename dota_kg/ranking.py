"""One ranking model over RDF effects, weighted rules and gameplay parameters.

No hero identities, answer lists, Parquet reads or network requests live here.
The graph is treated as an immutable snapshot for the lifetime of a model.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import re
from statistics import median

from rdflib import Graph, RDF, RDFS, URIRef

from .ontology import literal
from .ontology_schema import KG, RES

SECOND_ABILITY = .3
OTHER_GROUPS = .4
REUSED_ABILITY = .25
REVERSE_WEIGHT = .2
STAT_LIMIT = .1
STAT_PRIOR = 300
DEFAULT_LEVEL = 18


@dataclass(frozen=True)
class Fact:
    uri: URIRef
    owner: URIRef
    code: str
    recipient: str
    conditions: tuple
    evidence: str


@dataclass(frozen=True)
class Need:
    code: str
    importance: float
    facts: tuple[Fact, ...]


@dataclass(frozen=True)
class Rule:
    uri: URIRef
    source: str
    target: str
    family: str
    weight: float
    scopes: frozenset[str]
    purpose: str = "counter"


@dataclass(frozen=True)
class Application:
    fact: Fact
    rule: Rule
    need: Need
    value: float


@dataclass
class Assessment:
    hero: URIRef
    enemy: URIRef
    score: float
    mechanical: float
    reverse: float
    statistical: float
    groups: list

    @property
    def primary(self):
        return self.groups[0]["applications"][0] if self.groups else None


class RankingModel:
    def __init__(self, graph, *, own_items=(), enemy_items=(), level=DEFAULT_LEVEL, use_statistics=True):
        if not 1 <= level <= 30:
            raise ValueError("Уровень для сравнения должен быть от 1 до 30.")
        self.graph = graph
        self.level = level
        self.own_items, self.enemy_items = frozenset(own_items), frozenset(enemy_items)
        self.heroes = tuple(sorted(graph.subjects(RDF.type, KG.Hero), key=str))
        self.items = frozenset(graph.subjects(RDF.type, KG.Item))
        if (self.own_items | self.enemy_items) - self.items:
            raise ValueError("В контексте указан неизвестный предмет.")
        self.abilities = {h: tuple(sorted(graph.objects(h, KG.hasAbility), key=str)) for h in self.heroes}
        self.ability_hero = {a: h for h, abilities in self.abilities.items() for a in abilities}
        self.stats = {h: {name: self.value(h, KG[name]) for name in
                      ("health", "strength", "strengthGain", "attackType", "mana")}
                      for h in self.heroes}
        slopes = [(a["health"] - b["health"]) / (a["strength"] - b["strength"])
                  for a in self.stats.values() for b in self.stats.values()
                  if all(type(v) in (int, float) for v in (a["health"], b["health"], a["strength"], b["strength"]))
                  and a["strength"] != b["strength"]]
        self.hp_per_strength = median(slopes) if slopes else None
        self.parameters = {}
        self.native = {}
        self.owner_facts = {}
        owners = set(self.ability_hero) | self.items
        for owner in sorted(owners, key=str):
            parameters = []
            for p in sorted(graph.objects(owner, KG.hasParameter), key=str):
                # A facet is never silently selected for the actor.
                if self.value(p, KG.requiredFacet, ""):
                    continue
                heading = re.sub(r"<[^>]+>", "", self.value(p, KG.parameterHeading, "")).upper()
                values = self.series(p)
                parameters.append({"uri": p, "name": self.value(p, KG.parameterName, ""),
                                   "heading": heading, "values": values,
                                   "percentage": self.value(p, KG.isPercentage, False)})
            self.parameters[owner] = parameters
            native = {}
            for channel, prop, aliases in (("cooldown", KG.hasCooldown, ("AbilityCooldown", "cooldown")),
                                            ("duration", KG.hasDuration, ("AbilityDuration", "duration"))):
                choices = list(graph.objects(owner, prop))
                preferred = [p for p in choices if self.value(p, KG.parameterName) in aliases]
                values = self.series((preferred or choices)[0]) if preferred or choices else []
                native[channel] = values[-1] if values else 0
            self.native[owner] = native
            facts = []
            for effect in sorted(graph.objects(owner, KG.hasEffect), key=str):
                recipient = self.value(effect, KG.effectTarget)
                if recipient is None:
                    continue  # The refined dataset is authoritative for ranking.
                mechanic = graph.value(effect, KG.effectMechanic)
                if mechanic is not None:
                    facts.append(Fact(effect, owner, self.code(mechanic), recipient,
                                      tuple(sorted(graph.objects(effect, KG.requiresCondition), key=str)),
                                      self.value(effect, KG.evidence, "")))
            self.owner_facts[owner] = tuple(facts)
        self.feature_facts = defaultdict(dict)
        for hero in self.heroes:
            for node in graph.objects(hero, KG.hasFeatureValue):
                if self.value(node, KG.isObserved) is not True or self.value(node, KG.value, 0) <= 0:
                    continue
                feature = graph.value(node, KG.feature)
                code = self.value(feature, KG.identifier) or self.code(feature)
                represented = list(graph.objects(feature, KG.featureMechanic))
                if represented:
                    code = self.code(represented[0])
                self.feature_facts[hero][code] = node
        self.rules = []
        for node in sorted(graph.subjects(RDF.type, KG.CounterRule), key=str):
            weight = self.value(node, KG.ruleWeight)
            purpose = self.value(node, KG.rulePurpose, "counter")
            if weight is None and purpose != "compensate":
                continue
            if weight is None:
                weight = 1.
            if type(weight) not in (float, int) or not math.isfinite(weight) or weight <= 0:
                raise ValueError("Вес правила должен быть конечным положительным числом.")
            source, target = graph.value(node, KG.sourceMechanic), graph.value(node, KG.targetMechanic)
            if source is not None and target is not None:
                self.rules.append(Rule(node, self.code(source), self.code(target),
                    self.value(node, KG.ruleFamily, "compensation"), float(weight),
                    frozenset(str(v) for v in graph.objects(node, KG.sourceEffectTarget)) or
                    frozenset({"self", "ally", "enemy", "area", "both"}), purpose))
        self.rules_by_source = defaultdict(list)
        for rule in self.rules:
            self.rules_by_source[rule.source].append(rule)
        self.matchups = {}
        if use_statistics:
            for hero in self.heroes:
                for node in graph.objects(hero, KG.hasMatchup):
                    self.matchups[(hero, graph.value(node, KG.opponent))] = (
                        self.value(node, KG.normalizedAdvantage), self.value(node, KG.matches, 0))
        self._profiles = {}
        self._assessments = {}
        self._strength = {}
        self.view = Graph()
        self._target_nodes = {}

    def value(self, node, prop, default=None):
        value = self.graph.value(node, prop) if node is not None else None
        return value.toPython() if value is not None else default

    @staticmethod
    def code(node):
        return str(node).rstrip('/').rsplit('/', 1)[-1].rsplit('#', 1)[-1]

    def series(self, parameter):
        values = sorted(self.graph.objects(parameter, KG.hasBaseValue),
                        key=lambda node: self.value(node, KG.position, 0))
        return [self.value(node, KG.numericValue) for node in values]

    def conditions_available(self, fact, held, opponent=()):
        for condition in fact.conditions:
            if self.value(condition, KG.conditionKind) != "has-item":
                continue
            equipment = opponent if self.value(condition, KG.conditionScope) == "target" else held
            if self.graph.value(condition, KG.conditionItem) not in equipment:
                return False
        return True

    def profile(self, hero, held=()):
        held = frozenset(held)
        key = hero, held
        if key in self._profiles:
            return self._profiles[key]
        facts = tuple(f for owner in (*self.abilities.get(hero, ()), *sorted(held, key=str))
                      for f in self.owner_facts.get(owner, ())
                      if self.conditions_available(f, held)
                      and not (owner in self.items and f.code == "CLEAVE_DAMAGE"
                               and self.stats.get(hero, {}).get("attackType") != "Melee"))
        by_code, by_owner = defaultdict(list), defaultdict(set)
        for fact in facts:
            by_code[fact.code].append(fact)
            by_owner[fact.owner].add(fact.code)
        codes = set(by_code)
        needs = {}

        def put(code, importance=1., witnesses=()):
            witnesses = tuple(sorted(set(witnesses), key=lambda f: str(f.uri)))
            if witnesses:
                needs[code] = Need(code, importance, witnesses)

        def witnesses(*mechanics):
            return [f for code in mechanics for f in by_code.get(code, ())]

        for code in sorted(codes):
            put(code, witnesses=witnesses(code))
        if codes & {"CREATES_ILLUSIONS", "ILLUSION_DEPENDENCE", "SUMMONS_UNITS", "CLONES"}:
            put("GROUPED_UNITS", 1.2 if "ILLUSION_DEPENDENCE" in codes else
                .55 if "SUMMONS_UNITS" in codes else .5,
                witnesses("CREATES_ILLUSIONS", "ILLUSION_DEPENDENCE", "SUMMONS_UNITS", "CLONES"))
        if "CREATES_ILLUSIONS" in codes:
            put("ILLUSIONS", 1.2 if "ILLUSION_DEPENDENCE" in codes else .6, witnesses("CREATES_ILLUSIONS"))
        recovery = codes & {"HEAL", "HEALTH_REGEN", "LIFESTEAL", "SPELL_LIFESTEAL", "DAMAGE_TO_HEALING"}
        if recovery:
            put("RECOVERY", 1.4 if "LOW_HEALTH_SCALING" in codes else .8, witnesses(*sorted(recovery)))
        for code in ("PASSIVE_DEFENSE", "DIRECTIONAL_DEFENSE", "CHANNELING", "MANA_SHIELD", "FORCED_COMBAT"):
            if code in codes:
                put(code, 1.25 if code in {"PASSIVE_DEFENSE", "DIRECTIONAL_DEFENSE"} else 1., witnesses(code))
        if "LOW_HEALTH_SCALING" in codes:
            put("LOW_HEALTH", 1.6, witnesses("LOW_HEALTH_SCALING"))
        elif "ATTRIBUTE_SHIFT" in codes:
            put("LOW_HEALTH", .6, witnesses("ATTRIBUTE_SHIFT"))
        if "ATTRIBUTE_SHIFT" in codes:
            for code, importance in (("HEALTH_STATE_DEFENSE", 1.3), ("ACTIVE_DEFENSE", 1.25), ("MANA_DEFENSE", .9)):
                put(code, importance, witnesses("ATTRIBUTE_SHIFT"))
        mobility = codes & {"BLINK", "DASH", "TELEPORT", "LEAP", "GAP_CLOSER"}
        if mobility:
            put("MOBILITY", .8 if codes & {"BLINK", "DASH", "TELEPORT"} else .4, witnesses(*sorted(mobility)))
        if "EVASION" in codes:
            put("EVASION", .35 if "GROUPED_UNITS" in needs else .8, witnesses("EVASION"))
        if "DIRECTIONAL_DEFENSE" in codes:
            for code in ("ATTRITION_DEFENSE", "LONG_FIGHT"):
                put(code, witnesses=witnesses("DIRECTIONAL_DEFENSE"))
        elif "PASSIVE_DEFENSE" in codes:
            put("ATTRITION_DEFENSE", .6, witnesses("PASSIVE_DEFENSE"))
        if "STACKING_EFFECT" in codes and codes & {"ARMOR_REDUCTION", "SLOW"}:
            put("STACKING_DEBUFF", .6, witnesses("STACKING_EFFECT", "ARMOR_REDUCTION", "SLOW"))
        if "PHYSICAL_DAMAGE" in codes or "FORCED_COMBAT" in codes:
            put("PHYSICAL_PRESSURE", .7, witnesses("PHYSICAL_DAMAGE", "FORCED_COMBAT"))
        if "LOW_HEALTH_SCALING" in codes and "MAGIC_RESISTANCE_BUFF" in codes:
            put("HIGH_MAGIC_RESISTANCE", witnesses=witnesses("MAGIC_RESISTANCE_BUFF"))
        for code, node in self.feature_facts.get(hero, {}).items():
            if code in {"HIGH_ARMOR", "LOW_ARMOR"} or not facts:
                put(code, .5 if code == "HIGH_ARMOR" else 1.,
                    (Fact(node, hero, code, "self", (), self.value(node, KG.evidence, "")),))
        active = [a for a in self.abilities.get(hero, ()) if not int(self.value(a, KG.behavior, 0)) & 2 and a in by_owner]
        active_facts = [f for f in facts if f.owner in active]
        if len(active) >= 2:
            put("SPELL_CASTING", .8, active_facts)
            if self.stats.get(hero, {}).get("attackType") == "Ranged":
                put("RANGED_CASTER", .6, active_facts)
        attack = codes & {"CLEAVE_DAMAGE", "MULTI_TARGET_ATTACK", "CRITICAL_STRIKE", "ATTACK_SPEED_BUFF",
                          "ATTACK_SCALING", "MANA_BURN", "ATTRIBUTE_STEAL"}
        if attack:
            put("ATTACK_DEPENDENCE", witnesses=witnesses(*sorted(attack)))
            put("PHYSICAL_PRESSURE", witnesses=witnesses(*sorted(attack)))
        magical = [a for a in active if "MAGICAL_DAMAGE" in by_owner[a]]
        if len(magical) >= 2:
            put("MAGICAL_PRESSURE", .8, [f for f in witnesses("MAGICAL_DAMAGE") if f.owner in magical])
        targeted = [a for a in active if int(self.value(a, KG.behavior, 0)) & 8 and int(self.value(a, KG.targetTeam, 0)) & 2]
        if targeted:
            put("TARGETED_SPELLS", .7, [f for f in active_facts if f.owner in targeted])
        defensive = {"DAMAGE_REDUCTION", "DAMAGE_BLOCK", "ARMOR_BUFF", "HEALTH_REGEN", "EVASION", "MAGIC_RESISTANCE_BUFF"}
        passive = [a for a in self.abilities.get(hero, ())
                   if int(self.value(a, KG.behavior, 0)) & 2 and by_owner.get(a, set()) & defensive]
        if passive and "PASSIVE_DEFENSE" not in needs:
            put("PASSIVE_DEFENSE", .6, [f for f in facts if f.owner in passive and f.code in defensive])
        result = facts, by_code, by_owner, needs
        self._profiles[key] = result
        return result

    def parameter_values(self, owner):
        return [(p, p["heading"], max(p["values"]) if p["values"] else 0) for p in self.parameters.get(owner, ())]

    def strength(self, fact, hero, enemy, held, illusion_count=0):
        key = fact.owner, fact.code, hero, enemy, frozenset(held), illusion_count
        if key in self._strength:
            return self._strength[key]
        owner, code = fact.owner, fact.code
        ps = self.parameter_values(owner)
        codes = self.profile(hero, held)[2] if hero in self.stats else {}
        value = 1.
        if code == "SUSTAINED_AOE":
            amounts = [v for p, h, v in ps if not p["percentage"] and ("DAMAGE PER SECOND" in h or "DPS" in h) and v > 0]
            if not amounts and re.search(r"one per second|each second", self.value(owner, KG.description, ""), re.I):
                amounts = [v for p, h, v in ps if h.rstrip(':') == "DAMAGE" and not p["percentage"] and v > 0]
            value = math.sqrt(max(amounts) / 100) if amounts else 1.
            if not amounts:
                window = [v for p, h, v in ps if "DURATION" in h and not any(w in h for w in
                          ("SLOW", "DEBUFF", "PENALTY", "BUFF", "STEAL")) and v > 0]
                if window:
                    value *= min(1, max(window) / 6)
            limits = [v for p, h, v in ps if h.rstrip(':') in {"UNIT COUNT", "MAX TARGETS", "TARGETS"} and v > 0]
            if limits:
                value *= min(1, max(limits) / 5)
            durations = [v for p, h, v in ps if h.rstrip(':') == "DURATION" and v > 0]
            cooldown = self.native.get(owner, {}).get("cooldown", 0)
            if durations and cooldown > 0:
                value *= min(1, math.sqrt(max(durations) / cooldown))
            if "PURE_DAMAGE" in codes.get(owner, set()):
                value *= 1.15
            value = max(.15, min(1.6, value))
        elif code in {"STUN", "HEX", "SILENCE", "ROOT", "LEASH", "TAUNT"}:
            durations = [v for p, h, v in ps if h.rstrip(':') in {code + " DURATION", "DURATION"} and v > 0]
            value = max(.4, min(1.8, math.sqrt(min(durations) / 3))) if durations else 1.
        elif code == "HEALTH_EXECUTION":
            thresholds = [v / 100 for p, h, v in ps if h.rstrip(':') in
                          {"SHATTER THRESHOLD", "KILL THRESHOLD", "SHATTER HEALTH THRESHOLD"} and v > 0]
            if not thresholds:
                damages = [v for p, h, v in ps if h.rstrip(':') == "DAMAGE" and v > 0]
                stats = self.stats.get(enemy, {})
                if damages and self.hp_per_strength is not None and all(type(stats.get(k)) in (int, float) for k in ("health", "strengthGain")):
                    hp = stats["health"] + stats["strengthGain"] * (self.level - 1) * self.hp_per_strength
                    if hp > 0:
                        thresholds = [max(damages) / hp]
            value = max(.3, min(1.5, max(thresholds) / .35)) if thresholds else 1.
        elif code in {"BREAK", "FORCED_FACING"}:
            durations = [v for p, h, v in ps if h.rstrip(':') in
                         {"BREAK DURATION", "DURATION", "MAX DEBUFF DURATION"} and v > 0]
            value = min(1.3, math.sqrt(min(durations) / 6)) if durations else 1.
        elif code == "MISSING_HEALTH_DAMAGE":
            values = [v for p, h, v in ps if "DAMAGE PER MISSING HP" in h and v > 0]
            if values:
                value = max(.3, min(1.5, (max(values) / (1 + max(values))) / .35))
            elif int(self.value(owner, KG.behavior, 0)) & 2:
                value = .5
        elif code == "CLEAVE_DAMAGE":
            amounts = [v for p, h, v in ps if "CLEAVE DAMAGE" in h and v > 0]
            value = math.sqrt(max(amounts) / 80) if amounts else .8
            if owner not in self.items and not int(self.value(owner, KG.behavior, 0)) & 2:
                value *= .65
            buff = 1.
            for ability in self.abilities.get(hero, ()):
                if "ATTACK_SCALING" not in codes.get(ability, set()):
                    continue
                for p, h, v in self.parameter_values(ability):
                    if h.rstrip(':') == "BONUS DAMAGE" and v > 0:
                        cooldown = self.native[ability]["cooldown"]
                        duration = self.native[ability]["duration"] or max(
                            [vv for pp, hh, vv in self.parameter_values(ability) if hh.rstrip(':') == "DURATION"] or [0])
                        uptime = min(1, duration / cooldown) if cooldown > 0 and duration > 0 else .5
                        buff = max(buff, math.sqrt(1 + v / 100 * uptime))
            value = max(.35, min(1.7, value * buff))
        elif code == "ATTACK_TRIGGERED_AOE":
            amounts = [v for p, h, v in ps if h.rstrip(':') == "DAMAGE" and v > 0]
            thresholds = [v for p, h, v in ps if h.rstrip(':') == "DAMAGE THRESHOLD" and v > 0]
            value = .4 if thresholds else min(1.3, math.sqrt(max(amounts) / 160)) if amounts else .75
        elif code == "BONUS_DAMAGE_PER_TARGET":
            amounts = [v for p, h, v in ps if h.rstrip(':') in {"ECHO DAMAGE", "DAMAGE PER CREEP", "DAMAGE PER UNIT"} and v > 0]
            echoes = [v for p, h, v in ps if h.rstrip(':') == "ECHO DAMAGE" and v > 0]
            # Each nearby illusion hit can emit an echo. The existing strength cap bounds the full-group estimate.
            if echoes and illusion_count > 1:
                amounts.append(max(echoes) * illusion_count)
            value = max(.4, min(1.6, math.sqrt(max(amounts) / 80))) if amounts else 1.
        elif code == "AOE_DAMAGE":
            amounts = [v for p, h, v in ps if h.rstrip(':') in {"DAMAGE", "INITIAL DAMAGE"} and not p["percentage"] and v > 0]
            value = max(.5, min(1.25, math.sqrt(max(amounts) / 300))) if amounts else 1.
        self._strength[key] = value
        return value

    def applications(self, hero, enemy, *, owner=None, items=False, focus=None, purpose="counter", needs=None, deduplicate=True):
        held = self.own_items
        facts, source_codes, source_owners, _ = self.profile(hero, held)
        target_facts, target_codes, target_owners, known_needs = self.profile(enemy, self.enemy_items)
        needs = known_needs if needs is None else needs
        if items:
            owners = (owner,) if owner is not None else self.items
            facts = [f for item in owners for f in self.owner_facts.get(item, ())
                     if self.value(item, KG.isPurchasable) is True
                     and self.conditions_available(f, held | {item}, self.enemy_items)
                     and not (f.code == "CLEAVE_DAMAGE" and hero in self.stats and self.stats[hero]["attackType"] != "Melee")]
        elif owner is not None:
            facts = [f for f in facts if f.owner == owner]
        return self._applications(hero, enemy, facts, source_codes, source_owners, target_codes,
                                  target_owners, needs, held, self.enemy_items, focus, purpose, deduplicate)

    def _applications(self, hero, enemy, facts, source_codes, source_owners, target_codes,
                      target_owners, needs, held, enemy_held, focus, purpose, deduplicate=True):
        hits = {}
        for fact in facts:
            allowed = True
            for condition in fact.conditions:
                if self.value(condition, KG.conditionKind) != 'has-mechanic':
                    continue
                required = self.code(self.graph.value(condition, KG.conditionMechanic))
                target_scope = self.value(condition, KG.conditionScope) == 'target'
                known = target_codes if target_scope else source_codes
                actor = enemy if target_scope else hero
                melee = required == 'MELEE' and self.stats.get(actor, {}).get('attackType') == 'Melee'
                if required not in known and not melee:
                    # Unknown actor for a proposed purchase retains its explicit condition.
                    if actor is not None:
                        allowed = False
            if not allowed:
                continue
            if not self.conditions_available(fact, held | ({fact.owner} if fact.owner in self.items else set()), enemy_held):
                continue
            for rule in self.rules_by_source.get(fact.code, ()):
                if rule.purpose != purpose or rule.target not in needs or fact.recipient not in rule.scopes:
                    continue
                if focus is not None and rule.family not in focus and rule.target not in focus:
                    continue
                if rule.family == "mana" and "MANALESS" in target_codes:
                    continue
                if rule.family == "units" and fact.code == "MISSING_MANA_DAMAGE":
                    if "AOE_DAMAGE" not in source_owners.get(fact.owner, set()) or "MANALESS" in target_codes:
                        continue
                    if not self.stats.get(enemy, {}).get("mana") or not ({"MANA_BURN", "MANA_DRAIN"} & source_codes.keys()):
                        continue
                need = needs[rule.target]
                if need.facts and not any(self.conditions_available(f, enemy_held, held) for f in need.facts):
                    continue
                illusion_count = 0
                if fact.code == "BONUS_DAMAGE_PER_TARGET" and need.code == "GROUPED_UNITS":
                    illusion_count = max((v for witness in need.facts if witness.code == "CREATES_ILLUSIONS"
                                          and self.conditions_available(witness, enemy_held, held)
                                          for p, heading, v in self.parameter_values(witness.owner)
                                          if heading.rstrip(':') == "MAX ILLUSIONS"), default=0)
                value = rule.weight * need.importance * self.strength(fact, hero, enemy, held, illusion_count)
                if rule.family == "units" and fact.code in {"CLEAVE_DAMAGE", "MULTI_TARGET_ATTACK"} and "HIGH_ARMOR" in needs:
                    value *= .85
                if rule.family == "control":
                    value *= {1: 1.1, 2: 1., 3: 1.3}.get(self.value(fact.owner, KG.dispellableCode), 1.)
                if fact.code in {"SUSTAINED_AOE", "DAMAGE_OVER_TIME"} and self.value(fact.owner, KG.dispellableCode) == 2 and "BASIC_DISPEL" in target_codes:
                    value *= .5
                if fact.code != "SUSTAINED_AOE":
                    cooldown = self.native.get(fact.owner, {}).get("cooldown", 0)
                    value *= .7 + .3 * min(1, 20 / max(1, cooldown))
                if rule.target == "PASSIVE_DEFENSE" and need.facts:
                    value *= max(.45 if "BREAK_PRESERVES_STACKS" in target_owners.get(f.owner, set()) else 1. for f in need.facts)
                if fact.code == "ANTI_ILLUSION" and int(self.value(fact.owner, KG.behavior, 0)) & 8:
                    value *= 1.3
                application = Application(fact, rule, need, value)
                key = (rule.family, fact.owner) if deduplicate else (rule.uri, fact.uri)
                previous = hits.get(key)
                order = lambda app: (-app.value, len(app.fact.conditions), app.fact.code, app.rule.target, str(app.fact.uri))
                if previous is None or order(application) < order(previous):
                    hits[key] = application
        return tuple(sorted(hits.values(), key=lambda a: (-a.value, a.rule.family, str(a.fact.owner))))

    @staticmethod
    def aggregate(applications):
        families = defaultdict(list)
        for application in applications:
            families[application.rule.family].append(application)
        groups = []
        for family, items in families.items():
            ordered = sorted(items, key=lambda a: (-a.value, len(a.fact.conditions), str(a.fact.owner), a.fact.code, a.rule.target))[:3]
            value = sum(app.value * SECOND_ABILITY ** i for i, app in enumerate(ordered))
            groups.append({"family": family, "value": value, "applications": ordered})
        groups.sort(key=lambda g: (-g["value"], g["family"]))
        used, total = set(), 0.
        for index, group in enumerate(groups):
            reused = group["applications"][0].fact.owner in used
            contribution = group["value"] * (REUSED_ABILITY if reused else 1.) * (OTHER_GROUPS if index else 1.)
            group["contribution"] = contribution
            total += contribution
            used.update(app.fact.owner for app in group["applications"])
        return total, groups

    def _mechanical(self, hero, enemy, own_items, enemy_items):
        facts, source_codes, source_owners, _ = self.profile(hero, own_items)
        _, target_codes, target_owners, needs = self.profile(enemy, enemy_items)
        apps = self._applications(hero, enemy, facts, source_codes, source_owners, target_codes,
                                 target_owners, needs, own_items, enemy_items, None, "counter")
        return self.aggregate(apps)

    def assess(self, hero, enemy):
        key = hero, enemy
        if key in self._assessments:
            return self._assessments[key]
        if hero not in self.stats or enemy not in self.stats or hero == enemy:
            return None
        mechanical, groups = self._mechanical(hero, enemy, self.own_items, self.enemy_items)
        if not groups:
            self._assessments[key] = None
            return None
        reverse, _ = self._mechanical(enemy, hero, self.enemy_items, self.own_items)
        advantage, matches = self.matchups.get(key, (None, 0))
        stat = STAT_LIMIT * math.tanh(matches / (matches + STAT_PRIOR) * advantage / .05) if advantage is not None and matches > 0 else 0.
        score = mechanical - REVERSE_WEIGHT * reverse + stat
        if not math.isfinite(score):
            raise ValueError("Невозможно вычислить конечный балл противодействия.")
        result = Assessment(hero, enemy, score, mechanical, reverse, stat, groups)
        self._assessments[key] = result
        return result

    def rank(self, enemy, hero=None):
        results = [result for candidate in (self.heroes if hero is None else (hero,))
                   if (result := self.assess(candidate, enemy)) is not None]
        return sorted(results, key=lambda r: (-r.score, str(r.hero)))

    def target_node(self, application, enemy):
        """Expose inferred gameplay mechanics in a temporary SPARQL view only."""
        need = application if isinstance(application, Need) else application.need
        witness = need.facts[0]
        if (witness.uri, KG.effectMechanic, RES[f"mechanic/{need.code}"]) in self.graph:
            return witness.uri
        owner = witness.owner
        equipment = '-'.join(sorted(self.code(item) for item in self.enemy_items)) or 'base'
        key = enemy, need.code, equipment
        if key not in self._target_nodes:
            uri = URIRef(f"{owner}/derived-effect/{need.code}/for-{self.code(enemy)}/{equipment}")
            self.view.add((owner, KG.hasEffect, uri))
            self.view.add((uri, RDF.type, KG.Effect))
            self.view.add((uri, KG.effectMechanic, RES[f"mechanic/{need.code}"]))
            self.view.add((uri, RDFS.label, literal(self.value(RES[f"mechanic/{need.code}"], RDFS.label, need.code))))
            self.view.add((uri, KG.effectStatus, literal("structured-description")))
            self.view.add((uri, KG.evidence, literal(' '.join(dict.fromkeys(f.evidence for f in need.facts if f.evidence)))))
            self._target_nodes[key] = uri
        return self._target_nodes[key]
