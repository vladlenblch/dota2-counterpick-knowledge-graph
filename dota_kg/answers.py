"""Twelve competency tasks, answered by traversing the supplied RDF graph."""
import re

from rdflib import RDF, RDFS

from .ontology_schema import KG, RES
from .questions import ROLES, QuestionParser


class KnowledgeBase:
    def __init__(self, graph):
        self.graph = graph
        self.parser = QuestionParser(graph)
        self.heroes = sorted(set(graph.subjects(RDF.type, KG.Hero)), key=str)
        self.owners = sorted(set(graph.subjects(KG.hasEffect, None)), key=str)
        self.rules = sorted(set(graph.subjects(RDF.type, KG.CounterRule)), key=str)
        self._ranking = None
        self._own_items = frozenset()
        self._enemy_items = frozenset()
        self._level = 18

    @property
    def ranking(self):
        if self._ranking is None:
            from .ranking import RankingModel
            self._ranking = RankingModel(self.graph, own_items=self._own_items,
                                         enemy_items=self._enemy_items, level=self._level)
        return self._ranking

    def application_row(self, application, **extra):
        # A concise answer uses representative source abilities; the complete
        # supporting facts remain available in JSON for inspection.
        facts, owners = [], set()
        for fact in application.need.facts:
            if fact.owner not in owners:
                owners.add(fact.owner)
                facts.append(fact.uri)
            if len(facts) == 2:
                break
        row = self.candidate_row(application.fact.owner, application.fact.uri,
                                 application.rule.uri, facts, **extra)
        row['mechanic_score'] = round(application.value, 6)
        row['family'] = application.rule.family
        row['target_facts'] = sorted({str(f.uri) for f in application.need.facts})
        return row

    def value(self, node, prop, default=None):
        value = self.graph.value(node, prop)
        return default if value is None else value.toPython()

    def label(self, node):
        return str(self.graph.value(node, RDFS.label) or node)

    def is_item(self, node):
        return (node, RDF.type, KG.Item) in self.graph

    def effects(self, owner):
        effects = list(self.graph.objects(owner, KG.hasEffect))
        detailed = {self.graph.value(effect, KG.effectMechanic) for effect in effects
                    if self.graph.value(effect, KG.effectTarget) is not None}
        # Detailed facts retain recipients and activation conditions. Coarse
        # observations remain available for mechanics without such a description.
        effects = [effect for effect in effects
                   if self.graph.value(effect, KG.effectTarget) is not None
                   or self.graph.value(effect, KG.effectMechanic) not in detailed]
        return sorted(effects, key=lambda effect: (
            len(list(self.graph.objects(effect, KG.requiresCondition))), str(effect)))

    def alternative_facts(self, facts):
        """Each fact is an alternative proof, not another required condition."""
        facts = sorted(facts, key=lambda fact: (
            len(list(self.graph.objects(fact, KG.requiresCondition))), str(fact)))
        unconditional = [fact for fact in facts if not list(self.graph.objects(fact, KG.requiresCondition))]
        return unconditional or facts[:1]

    def abilities(self, hero):
        return sorted(self.graph.objects(hero, KG.hasAbility), key=str)

    def traits(self, hero):
        """Only observed, positive facts and effects supported by abilities."""
        traits = {}
        for ability in self.abilities(hero):
            for effect in self.effects(ability):
                mechanic = self.graph.value(effect, KG.effectMechanic)
                traits.setdefault(mechanic, []).append(effect)
        for fact in self.graph.objects(hero, KG.hasFeatureValue):
            if self.value(fact, KG.isObserved) is not True or self.value(fact, KG.value, 0) <= 0:
                continue
            feature = self.graph.value(fact, KG.feature)
            for mechanic in self.graph.objects(feature, KG.featureMechanic):
                if mechanic not in traits:
                    traits[mechanic] = [fact]
        return traits

    def conditions(self, *nodes):
        result = []
        for node in nodes:
            for prop in (KG.requiresCondition, KG.blockedByCondition):
                for condition in sorted(self.graph.objects(node, prop), key=str):
                    # Generic rule qualifications and blocker reminders are not
                    # user instructions. CQ11 checks blockers directly in RDF.
                    if ((node, RDF.type, KG.CounterRule) in self.graph
                            and (self.value(condition, KG.conditionKind) == "review"
                                 or prop == KG.blockedByCondition)):
                        continue
                    text = self.value(condition, KG.conditionText)
                    if text:
                        result.append(text)
        return list(dict.fromkeys(result))

    def candidate_row(self, owner, effect, rule, targets, **extra):
        source = self.graph.value(effect, KG.effectMechanic)
        target = self.graph.value(rule, KG.targetMechanic)
        targets = self.alternative_facts(targets)
        conditions = self.conditions(effect, rule)
        conditions.extend("У противника: " + text for text in self.conditions(*targets))
        evidence = str(self.value(effect, KG.evidence, ""))
        target_abilities = set()
        for fact in targets:
            target_abilities.update(self.graph.objects(fact, KG.supportedByAbility))
            target_abilities.update(ability for ability in self.graph.subjects(KG.hasEffect, fact)
                                    if (ability, RDF.type, KG.Ability) in self.graph)
        target_name = ", ".join(self.label(ability) for ability in sorted(target_abilities, key=str)) or self.label(target)
        answer = (f"{self.label(owner)}: {self.label(source)} против «{target_name}». "
                  + " ".join(conditions)).rstrip()
        source_hero = self.graph.value(owner, KG.hero)
        if source_hero is None:
            source_hero = next(self.graph.subjects(KG.hasAbility, owner), None)
        if source_hero:
            answer = f"{self.label(source_hero)} — " + answer
        return {"answer": answer, "source": str(owner), "effect": str(effect),
                "kind": "item" if self.is_item(owner) else "ability",
                "rule": str(rule), "rule_status": self.value(rule, KG.ruleStatus),
                "evidence": evidence, "target_facts": [str(t) for t in targets],
                "target_abilities": [str(a) for a in sorted(target_abilities, key=str)],
                "conditions": conditions, **extra}

    def roles(self, hero):
        positions = {self.value(hero, predicate) for predicate in
                     (KG.mostPopularPosition, KG.secondMostPopularPosition)}
        return [role for role in ROLES if role in positions]

    def cq01(self, q):
        from .ranking import SECOND_ABILITY
        rows = []
        for assessment in self.ranking.rank(q.enemy, q.hero):
            roles = self.roles(assessment.hero)
            if q.role:
                roles = [q.role] if q.role in roles else []
            reasons = []
            for group in assessment.groups:
                for index, application in enumerate(group['applications']):
                    reason = self.application_row(application)
                    reason['contribution'] = round(group['contribution'] *
                        (application.value * SECOND_ABILITY ** index / group['value']), 6)
                    reasons.append(reason)
            for role in roles:
                row = dict(reasons[0])
                row.update(hero=str(assessment.hero), role=role,
                           score=round(assessment.score, 6),
                           mechanical_score=round(assessment.mechanical, 6),
                           reverse_score=round(assessment.reverse, 6), reasons=reasons)
                if row['kind'] == 'item':
                    row['answer'] = f"{self.label(assessment.hero)} — {row['answer']}"
                row['answer'] = f"{ROLES[role]} против {self.label(q.enemy)}: {row['answer']}"
                rows.append(row)
        return sorted(rows, key=lambda r: (r['role'], -r['score'], r['hero']))

    def cq02(self, q):
        return [self.application_row(application, hero=str(q.hero), enemy=str(q.enemy))
                for application in self.ranking.applications(q.hero, q.enemy, owner=q.subject)]

    def series(self, parameter, predicate=KG.hasBaseValue):
        values = sorted(self.graph.objects(parameter, predicate),
                        key=lambda v: self.value(v, KG.position, 0))
        return [self.value(v, KG.numericValue) for v in values]

    def cq03(self, q):
        owners = self.abilities(q.subject) if (q.subject, RDF.type, KG.Hero) in self.graph else [q.subject]
        rows = []
        for owner in owners:
            conditions = self.conditions(owner, *self.effects(owner))
            mechanics = {self.graph.value(e, KG.effectMechanic) for e in self.effects(owner)}
            for rule in self.rules:
                if self.graph.value(rule, KG.sourceMechanic) in mechanics:
                    conditions.extend(self.conditions(rule))
            parameters = [{"uri": str(p), "name": self.value(p, KG.parameterName),
                           "values": self.series(p), "required_facet": self.value(p, KG.requiredFacet)}
                          for p in sorted(self.graph.objects(owner, KG.hasParameter), key=str)]
            notes = [self.value(n, KG.noteText) for n in self.graph.objects(owner, KG.hasNote)]
            details = list(dict.fromkeys(conditions + [n for n in notes if n]))
            if not details and not parameters:
                continue
            text = " ".join(details)
            if not text:
                continue
            rows.append({"source": str(owner), "conditions": details, "parameters": parameters,
                         "answer": f"{self.label(owner)}: {text}"})
        return rows

    def mechanic_task(self, q, focus):
        from .ranking import Need
        rows = []
        needs = None
        if not q.enemy:
            needs = {code: Need(code, 1., ()) for code in focus}
            if q.subject:
                facts = [f for profile in (self.ranking.profile(hero)[0] for hero in self.ranking.heroes)
                         for f in profile if f.owner == q.subject]
                needs = {code: Need(code, 1., tuple(f for f in facts if f.code == code))
                         for code in focus if any(f.code == code for f in facts)}
        heroes = [q.hero] if q.hero else self.ranking.heroes
        for hero in heroes:
            for app in self.ranking.applications(hero, q.enemy, focus=focus, needs=needs):
                rows.append(self.application_row(app))
        from .item_ranking import ItemRanking
        for assessment in ItemRanking(self.ranking).rank(q.hero, q.enemy, position=q.role,
                phase=q.phase, owner=q.item, focus=focus, needs=needs):
            row = self.application_row(assessment.primary)
            row['score'] = round(assessment.score, 6)
            rows.append(row)
        return sorted(rows, key=lambda r: (-r.get('score', r['mechanic_score']), r['source'], r['rule']))

    def cq04(self, q):
        return self.mechanic_task(q, {'CHANNELING'})

    def cq05(self, q):
        return self.mechanic_task(q, {'units', 'GROUPED_UNITS', 'ILLUSIONS'})

    def cq06(self, q):
        return self.mechanic_task(q, {'INVISIBILITY'})

    def cq07(self, q):
        targets = [q.subject] if q.subject else self.abilities(q.enemy) if q.enemy else self.owners
        rows = []
        for owner in targets:
            for effect in self.effects(owner):
                required = self.value(effect, KG.requiredDispel)
                if required is None:
                    continue
                if required == "None":
                    rows.append({"source": str(owner), "required": required,
                                 "answer": f"{self.label(owner)}: эффект «{self.label(self.graph.value(effect, KG.effectMechanic))}» не снимается развеиванием."})
                    continue
                for cleanser in self.owners:
                    if q.item and cleanser != q.item:
                        continue
                    scope = self.value(cleanser, KG.dispelTarget)
                    if scope not in ("self", "ally") or q.ally is True and scope != "ally":
                        continue
                    effects = self.effects(cleanser)
                    strong_effects = [e for e in effects
                                      if self.graph.value(e, KG.effectMechanic) == RES["mechanic/STRONG_DISPEL"]]
                    basic_effects = [e for e in effects
                                     if self.graph.value(e, KG.effectMechanic) == RES["mechanic/BASIC_DISPEL"]]
                    strong = bool(strong_effects)
                    usable = strong_effects or (basic_effects if required != "Strong" else [])
                    if not usable:
                        continue
                    conditions = self.conditions(*self.alternative_facts(usable))
                    rows.append({"source": str(owner), "effect": str(effect), "cleanser": str(cleanser),
                                 "required": required, "scope": scope, "conditions": conditions,
                                 "evidence": self.value(effect, KG.evidence),
                                 "answer": (f"{self.label(owner)}: {self.label(cleanser)} — {'сильное' if strong else 'обычное'} развеивание ({'на себе' if scope == 'self' else 'на союзнике'}). " + " ".join(conditions)).rstrip()})
        return rows

    def cq08(self, q):
        owners = [q.subject] if q.subject else self.abilities(q.enemy) if q.enemy else (
            sorted(set(self.graph.subjects(RDF.type, KG.Ability)), key=str))
        return [{"ability": str(owner), "pierces": self.value(owner, KG.piercesDebuffImmunity) is True,
                 "answer": f"{self.label(owner)}: " + ("проходит сквозь невосприимчивость к эффектам."
                 if self.value(owner, KG.piercesDebuffImmunity) is True else
                 "не проходит сквозь невосприимчивость к эффектам.")}
                for owner in owners if q.subject or self.value(owner, KG.piercesDebuffImmunity) is True]

    def item_rows(self, q, purpose="counter"):
        from .item_ranking import ItemRanking
        enemy = q.hero if purpose == "compensate" else q.enemy
        rows = []
        for assessment in ItemRanking(self.ranking).rank(q.hero, enemy, position=q.role,
                phase=q.phase, owner=q.item, purpose=purpose):
            row = self.application_row(assessment.primary, item=str(assessment.item))
            row.update(score=round(assessment.score, 6), mechanical_score=round(assessment.mechanical, 6),
                       suitability=round(assessment.suitability, 6), timing=round(assessment.timing, 6))
            row['answer'] = f"Для {self.label(q.hero)}: " + row['answer']
            row['reasons'] = [self.application_row(app) for group in assessment.groups for app in group['applications']]
            rows.append(row)
        return rows

    def cq09(self, q):
        return self.item_rows(q)

    def cq10(self, q):
        return self.item_rows(q, purpose="compensate")

    def cq11(self, q):
        item_mechanics = {self.graph.value(e, KG.effectMechanic) for e in self.effects(q.item)}
        rows = []
        owners = [q.subject] if q.subject else self.abilities(q.hero)
        for owner in owners:
            for effect in self.effects(owner):
                mechanic = self.graph.value(effect, KG.effectMechanic)
                for rule in self.rules:
                    if self.graph.value(rule, KG.sourceMechanic) != mechanic:
                        continue
                    if q.enemy and self.graph.value(rule, KG.targetMechanic) not in self.traits(q.enemy):
                        continue
                    for blocker in self.graph.objects(rule, KG.blockedByCondition):
                        block = self.graph.value(blocker, KG.conditionMechanic)
                        if block not in item_mechanics:
                            continue
                        pierces = self.value(owner, KG.piercesDebuffImmunity) is True
                        if q.active is False:
                            conclusion = "Предмет не активирован, его эффект не действует."
                        elif q.active is None:
                            conclusion = "Одного владения предметом недостаточно: уточните, действует ли его эффект."
                        elif block == RES["mechanic/DEBUFF_IMMUNITY"] and pierces:
                            conclusion = "Способность проходит сквозь невосприимчивость к эффектам."
                        else:
                            conclusion = "Активный эффект предмета ограничивает это противодействие."
                        rows.append({"source": str(owner), "item": str(q.item), "rule": str(rule),
                                     "active": q.active, "pierces": pierces,
                                     "answer": (f"{self.label(owner)} против {self.label(q.item)}: {conclusion} " + " ".join(self.conditions(effect, rule))).rstrip()})
        return rows

    def cq12(self, q):
        owners = [q.subject] if q.subject else self.abilities(q.hero)
        rows = []
        for owner in owners:
            for kind, description, series in (("scepter", KG.scepterDescription, KG.hasScepterValue),
                                              ("shard", KG.shardDescription, KG.hasShardValue)):
                if q.upgrade and q.upgrade != kind:
                    continue
                text = self.value(owner, description)
                parameters = [{"parameter": str(p), "name": self.value(p, KG.parameterName),
                               "base": self.series(p), "upgraded": self.series(p, series)}
                              for p in sorted(self.graph.objects(owner, KG.hasParameter), key=str)
                              if list(self.graph.objects(p, series))]
                granted = self.value(owner, KG.grantedByScepter if kind == "scepter" else KG.grantedByShard)
                if text or parameters or granted is True:
                    if not text and granted is not True:
                        continue
                    description = text or "Добавляет способность."
                    upgrade_name = "Aghanim's Scepter" if kind == "scepter" else "Aghanim's Shard"
                    rows.append({"ability": str(owner), "upgrade": kind, "evidence": text,
                                 "parameters": parameters, "grants_ability": granted is True,
                                 "answer": f"{self.label(owner)}, {upgrade_name}: {description}"})
            for parameter in sorted(self.graph.objects(owner, KG.hasParameter), key=str):
                for modifier in sorted(self.graph.objects(parameter, KG.hasModifier), key=str):
                    talent = self.graph.value(modifier, KG.requiresTalent)
                    kind = "talent" if talent else "facet" if self.value(modifier, KG.modifierKind) == "facet" else None
                    if kind is None or q.upgrade and q.upgrade != kind:
                        continue
                    name = self.label(talent) if talent else self.value(modifier, KG.conditionName)
                    value = self.value(modifier, KG.numericValue)
                    if value is None:
                        value = self.series(modifier)
                    rows.append({"ability": str(owner), "modifier": str(modifier), "upgrade": kind,
                                 "talent": str(talent) if talent else None, "value": value,
                                 "operation": self.value(modifier, KG.operation),
                                 "answer": f"{self.label(owner)}: {name}."})
        return rows

    def ask(self, text, limit=3, *, own_items=(), enemy_items=(), level=18):
        if limit < 1:
            raise ValueError("Число вариантов должно быть положительным.")
        q = self.parser.parse(text)
        self._own_items = frozenset(self.parser.resolve_entity(v, KG.Item) for v in own_items)
        self._enemy_items = frozenset(self.parser.resolve_entity(v, KG.Item) for v in enemy_items)
        self._level = level
        if q.cq in (1, 2) and q.item:
            if re.search(r'у противник|у врага|у него', text.casefold()):
                self._enemy_items |= {q.item}
            else:
                self._own_items |= {q.item}
        self._ranking = None
        rows = getattr(self, f"cq{q.cq:02d}")(q)
        # Select per role/entity rather than letting one hero's many effects fill the top.
        selected, counts, seen = [], {}, set()
        for row in rows:
            group = row.get("role", "all")
            if q.cq in (4, 5, 6):
                group = row["kind"]
            elif q.cq == 7:
                group = row.get("effect", row["source"])
            entity = row.get("hero") if q.cq == 1 else row.get("item") if q.cq in (9, 10) else None
            if q.cq in (4, 5, 6):
                entity = row['source']
            key = (group, entity) if entity else (group, row["answer"])
            if key in seen or counts.get(group, 0) >= limit:
                continue
            seen.add(key)
            counts[group] = counts.get(group, 0) + 1
            selected.append(row)
        notes = []
        missing_roles = []
        if q.cq == 1:
            missing_roles = [r for r in ([q.role] if q.role else ROLES) if not counts.get(r)]
        result = {"question": text, "cq": q.cq,
                "parameters": {k: str(v) if hasattr(v, "n3") else v for k, v in vars(q).items() if v is not None},
                "answers": selected, "missing_roles": missing_roles,
                "status": "answered" if selected else "no_data", "notes": notes}
        if self._own_items or self._enemy_items:
            result['equipment'] = {'own': sorted(map(str, self._own_items)),
                                   'enemy': sorted(map(str, self._enemy_items))}
        return result


def print_answer(result, output):
    for row in result["answers"]:
        print("- " + row["answer"], file=output)
    for role in result["missing_roles"]:
        print(f"- {ROLES[role]}: в размеченных данных подходящих вариантов не найдено.", file=output)
    if not result["answers"] and not result["missing_roles"]:
        print("В размеченных данных подходящих вариантов не найдено.", file=output)
    for note in result["notes"]:
        print(note, file=output)
