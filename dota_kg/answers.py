"""Twelve competency tasks, answered by traversing the supplied RDF graph."""
import re

from rdflib import RDF, RDFS, URIRef

from .ontology_schema import KG, RES, DISPELLABLE_BUFFS, DISPELLABLE_DEBUFFS
from .questions import ROLES, QuestionParser


class KnowledgeBase:
    def __init__(self, graph):
        self.graph = graph
        self.parser = QuestionParser(graph)
        self.owners = sorted(set(graph.subjects(KG.hasEffect, None)), key=str)
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

    def conditions(self, *nodes):
        result = []
        for node in nodes:
            for prop in (KG.requiresCondition, KG.blockedByCondition):
                for condition in sorted(self.graph.objects(node, prop), key=str):
                    # Generic rule qualifications and blocker reminders are not
                    # user instructions. CQ11 checks blockers directly in RDF.
                    if ((node, RDF.type, KG.CounterRule) in self.graph
                            and prop == KG.blockedByCondition):
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
                "rule": str(rule),
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

    def available_effects(self, owner, *, target=False):
        held, other = ((self._enemy_items, self._own_items) if target else
                       (self._own_items, self._enemy_items))
        if self.is_item(owner):
            held = held | {owner}
        return [effect for effect in self.effects(owner)
                if self.ranking.conditions_available(effect, held, other)]

    def cq03(self, q):
        from .game_text import render_game_text
        owners = self.abilities(q.subject) if (q.subject, RDF.type, KG.Hero) in self.graph else [q.subject]
        rows = []
        for owner in owners:
            effects = self.effects(owner)
            per_effect = {effect: self.conditions(effect) for effect in effects}
            common = set.intersection(*(set(v) for v in per_effect.values())) if effects else set()
            conditions = list(dict.fromkeys(self.conditions(owner) + sorted(common)))
            parameters = [{"uri": str(param), "name": self.value(param, KG.parameterName),
                           "values": self.series(param), "required_facet": self.value(param, KG.requiredFacet)}
                          for param in sorted(self.graph.objects(owner, KG.hasParameter), key=str)]
            notes = [self.value(note, KG.noteText) for note in self.graph.objects(owner, KG.hasNote)
                     if self.value(note, KG.noteKind) != 'facet']
            details = list(dict.fromkeys(conditions + [note for note in notes if note]))
            text = ' '.join(render_game_text(self.graph, owner, line) for line in details)
            if not text:
                text = render_game_text(self.graph, owner, self.value(owner, KG.description, ''))
            if text:
                rows.append({"source": str(owner), "conditions": conditions, "parameters": parameters,
                             "answer": f"{self.label(owner)}: {text}"})
            seen = set()
            for effect, requirements in per_effect.items():
                specific = [v for v in requirements if v not in common]
                if not specific:
                    continue
                mechanic = self.graph.value(effect, KG.effectMechanic)
                key = mechanic, tuple(specific)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"source": str(owner), "effect": str(effect), "conditions": specific,
                             "parameters": [], "conditional": True,
                             "answer": f"{self.label(owner)}, {self.label(mechanic)}: " + ' '.join(specific)})
        return rows

    def mechanic_task(self, q, focus, *, needs=None):
        from .ranking import Need
        rows = []
        if needs is None and not q.enemy:
            needs = {code: Need(code, 1., ()) for code in focus}
            if q.subject:
                owner_hero = self.ranking.ability_hero.get(q.subject)
                equipment = self._enemy_items | ({q.subject} if self.is_item(q.subject) else set())
                profile = self.ranking.profile(owner_hero, equipment)
                needs = {code: Need(code, need.importance, tuple(f for f in need.facts if f.owner == q.subject))
                         for code, need in profile[3].items() if any(f.owner == q.subject for f in need.facts)}
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
        profile = None
        if q.enemy:
            profile = self.ranking.profile(q.enemy, self._enemy_items)
        elif q.subject:
            owner = self.ranking.ability_hero.get(q.subject)
            equipment = self._enemy_items | ({q.subject} if self.is_item(q.subject) else set())
            profile = self.ranking.profile(owner, equipment)
        if profile is None:
            return self.mechanic_task(q, {'INVISIBILITY'})
        facts = [f for f in profile[0] if not q.subject or f.owner == q.subject]
        protected = {f.owner for f in facts if f.code == 'DETECTION_IMMUNITY'}
        visible_to_detection = tuple(f for f in facts if f.code == 'INVISIBILITY' and f.owner not in protected)
        from .ranking import Need
        needs = {'INVISIBILITY': Need('INVISIBILITY', 1., visible_to_detection)} if visible_to_detection else {}
        rows = []
        for owner in sorted(protected, key=str):
            witness = next(f for f in facts if f.owner == owner and f.code == 'DETECTION_IMMUNITY')
            rows.append({'source': str(owner), 'effect': str(witness.uri), 'evidence': witness.evidence,
                         'kind': 'item' if self.is_item(owner) else 'ability', 'counterable': False,
                         'answer': f'{self.label(owner)}: этот эффект не раскрывается обычным True Sight.'})
        if needs:
            rows.extend(self.mechanic_task(q, {'INVISIBILITY'}, needs=needs))
        return rows

    def cq07(self, q):
        targets = [q.subject] if q.subject else self.abilities(q.enemy) if q.enemy else self.owners
        grouped = {}
        for owner in targets:
            for effect in self.available_effects(owner, target=True):
                code = self.ranking.code(self.graph.value(effect, KG.effectMechanic))
                scope = self.value(effect, KG.effectTarget)
                if scope not in {'self', 'ally', 'enemy', 'area', 'both'}:
                    continue
                if code not in DISPELLABLE_DEBUFFS | DISPELLABLE_BUFFS or code in DISPELLABLE_DEBUFFS and scope == 'self':
                    continue
                required = self.value(effect, KG.requiredDispel)
                if required is None:
                    continue
                offensive = code in DISPELLABLE_BUFFS and scope in {'self', 'ally', 'both'}
                grouped.setdefault((owner, required, offensive), []).append(effect)
        rows = []
        for (owner, required, offensive), effects in grouped.items():
            effect = effects[0]
            label = ', '.join(dict.fromkeys(self.label(self.graph.value(e, KG.effectMechanic)) for e in effects))
            common = dict(source=str(owner), effect=str(effect), target_facts=list(map(str, effects)),
                          required=required, evidence=self.value(effect, KG.evidence), offensive=offensive)
            if required == 'None':
                rows.append({**common, 'kind': 'restriction',
                             'answer': f'{self.label(owner)}: {label} не снимается развеиванием.'})
                continue
            for cleanser in self.owners:
                if q.item and cleanser != q.item:
                    continue
                item = self.is_item(cleanser)
                if item and (self.value(cleanser, KG.isPurchasable) is not True or self.value(cleanser, KG.isNeutral) is True):
                    continue
                if not item and q.hero and cleanser not in self.abilities(q.hero):
                    continue
                candidates = []
                for purge in self.available_effects(cleanser):
                    code = self.ranking.code(self.graph.value(purge, KG.effectMechanic))
                    if code not in {'BASIC_DISPEL', 'STRONG_DISPEL', 'CONTINUOUS_DISPEL', 'ALLY_DISPEL'}:
                        continue
                    strong = code == 'STRONG_DISPEL'
                    if required == 'Strong' and not strong:
                        continue
                    scope = self.value(purge, KG.effectTarget)
                    if offensive:
                        if scope not in {'enemy', 'both'}:
                            continue
                    elif scope not in {'self', 'ally', 'both'} or q.ally is True and scope == 'self' or q.ally is False and scope == 'ally':
                        continue
                    candidates.append((strong, purge, scope))
                if not candidates:
                    continue
                strong, purge, scope = min(candidates, key=lambda c: (c[0], len(self.conditions(c[1])), str(c[1])))
                conditions = self.conditions(purge)
                if required == 'Strong' and scope == 'self' and not int(self.value(cleanser, KG.behavior, 0)) & 2:
                    if not self.value(cleanser, KG.passiveEffect):
                        conditions.append('Самостоятельное применение требуется до наступления оглушения.')
                direction = 'на противнике' if offensive else 'на союзнике' if scope == 'ally' else 'на себе' if scope == 'self' else 'на себе или союзнике'
                rows.append({**common, 'cleanser': str(cleanser), 'kind': 'item' if item else 'ability',
                             'scope': scope, 'conditions': conditions, 'strong': strong,
                             'answer': (f"{self.label(owner)}: {self.label(cleanser)} — {'сильное' if strong else 'обычное'} развеивание ({direction}). " + ' '.join(conditions)).rstrip()})
        return sorted(rows, key=lambda r: (r['source'], r['required'], r.get('strong', False),
                      r.get('scope') == 'self',
                      len(r.get('conditions', ())), self.value(URIRef(r.get('cleanser', '')), KG.cost, 0), r.get('cleanser', '')))

    def cq08(self, q):
        owners = [q.subject] if q.subject else self.abilities(q.enemy) if q.enemy else sorted(self.graph.subjects(RDF.type, KG.Ability), key=str)
        rows = []
        for owner in owners:
            effects = self.effects(owner)
            pierces = self.value(owner, KG.piercesDebuffImmunity) is True or any(
                self.graph.value(effect, KG.effectMechanic) == RES['mechanic/PIERCES_DEBUFF_IMMUNITY'] for effect in effects)
            hostile = [effect for effect in effects if self.value(effect, KG.effectTarget) in {'enemy', 'both'}
                       and self.graph.value(effect, KG.effectMechanic) != RES['mechanic/PIERCES_DEBUFF_IMMUNITY']]
            if not q.subject and (not pierces or not hostile):
                continue
            proofs = [e for e in effects if self.graph.value(e, KG.effectMechanic) == RES['mechanic/PIERCES_DEBUFF_IMMUNITY']]
            conditions = self.conditions(*self.alternative_facts(proofs))
            text = 'проходит сквозь невосприимчивость к эффектам.' if pierces else 'не проходит сквозь невосприимчивость к эффектам.'
            rows.append({'ability': str(owner), 'pierces': pierces, 'conditions': conditions,
                         'answer': (f'{self.label(owner)}: {text} ' + ' '.join(conditions)).rstrip()})
        return rows

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
        item_codes = {self.ranking.code(self.graph.value(e, KG.effectMechanic)) for e in self.effects(q.item)}
        defenses = item_codes & {'DEBUFF_IMMUNITY', 'SPELL_BLOCK', 'SPELL_REFLECTION'}
        if not defenses:
            return []
        apps = self.ranking.applications(q.hero, q.enemy, owner=q.subject) if q.enemy else ()
        applications = {}
        for app in apps:
            applications.setdefault(app.fact.owner, app)
        owners = ([q.subject] if q.subject else self.abilities(q.hero))
        rows = []
        for owner in owners:
            if q.enemy and owner not in applications:
                continue
            effects = self.available_effects(owner)
            relevant = [e for e in effects if self.value(e, KG.effectTarget) in {'enemy', 'both', 'area'}
                        and self.graph.value(e, KG.effectMechanic) != RES['mechanic/PIERCES_DEBUFF_IMMUNITY']]
            if not relevant:
                continue
            codes = {self.ranking.code(self.graph.value(e, KG.effectMechanic)) for e in relevant}
            all_codes = {self.ranking.code(self.graph.value(e, KG.effectMechanic)) for e in effects}
            unit = bool(int(self.value(owner, KG.behavior, 0)) & 8)
            point = bool(int(self.value(owner, KG.behavior, 0)) & 16)
            pierces = self.value(owner, KG.piercesDebuffImmunity) is True or 'PIERCES_DEBUFF_IMMUNITY' in all_codes
            conclusions = []
            if 'SPELL_BLOCK' in defenses:
                if q.active is False:
                    conclusions.append('Блок заклинания потрачен; повторного блока сейчас нет.')
                elif 'IGNORES_SPELL_BLOCK' in all_codes or not unit:
                    conclusions.append('Блок направленных заклинаний не срабатывает на эту способность.')
                else:
                    conclusions.append('Готовый блок останавливает направленное применение на владельца предмета.')
                    if point or 'AOE_DAMAGE' in all_codes:
                        conclusions.append('Попадание через точку или другую цель не является направленным применением на владельца.')
            if 'SPELL_REFLECTION' in defenses:
                if q.active is False:
                    conclusions.append('Эффект отражения не активирован.')
                elif 'IGNORES_SPELL_REFLECTION' in all_codes or not unit:
                    conclusions.append('Эта способность не вызывает отражение заклинания.')
                else:
                    conclusions.append(('Если отражение активно, ' if q.active is None else '') +
                                       'отразится копия заклинания; исходный эффект на противнике сохранится.')
            if 'DEBUFF_IMMUNITY' in defenses:
                if q.active is False:
                    conclusions.append('Предмет не активирован, его эффект не действует.')
                elif pierces:
                    conclusions.append(('При активном эффекте предмета ' if q.active is None else '') +
                                       'способность проходит сквозь невосприимчивость к эффектам.')
                else:
                    pieces = []
                    if codes & {'STUN', 'HEX', 'SILENCE', 'ROOT', 'SLOW', 'DISARM', 'LEASH', 'FEAR', 'TAUNT'}:
                        pieces.append('контроль блокируется невосприимчивостью')
                    if 'MAGICAL_DAMAGE' in all_codes:
                        pieces.append('магический урон уменьшается сопротивлением магии')
                    elif 'PHYSICAL_DAMAGE' in all_codes:
                        pieces.append('физический урон сохраняется')
                    elif 'PURE_DAMAGE' in all_codes:
                        pieces.append('чистый урон без прохода сквозь невосприимчивость блокируется')
                    if not pieces:
                        continue
                    conclusions.append(('Если эффект активен, ' if q.active is None else '') + '; '.join(pieces) + '.')
            if not conclusions:
                continue
            app = applications.get(owner)
            effect = app.fact.uri if app else relevant[0]
            rows.append({'source': str(owner), 'item': str(q.item), 'rule': str(app.rule.uri) if app else None,
                         'active': q.active, 'pierces': pierces, 'conditions': self.conditions(effect),
                         'evidence': self.value(effect, KG.evidence),
                         'answer': f'{self.label(owner)} против {self.label(q.item)}: ' + ' '.join(conclusions)})
        return rows

    def cq12(self, q):
        from .game_text import talent_text, facet_text, render_game_text
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
                    description = render_game_text(self.graph, owner, text,
                        values={p["name"]: p["upgraded"] for p in parameters}) if text else "Добавляет способность."
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
                    name = talent_text(self.graph, owner, talent) if talent else facet_text(self.graph, owner, self.value(modifier, KG.conditionName))
                    if not name:
                        continue
                    value = self.value(modifier, KG.numericValue)
                    if value is None:
                        value = self.series(modifier)
                    rows.append({"ability": str(owner), "modifier": str(modifier), "upgrade": kind,
                                 "talent": str(talent) if talent else None,
                                 "facet": self.value(modifier, KG.conditionName) if kind == "facet" else None,
                                 "parameter": str(parameter), "value": value,
                                 "operation": self.value(modifier, KG.operation),
                                 "answer": f"{self.label(owner)}: {name.rstrip('. ')}."})
        unique = {}
        for row in rows:
            key = row.get('ability'), row.get('upgrade'), row.get('talent') or row.get('facet')
            if row.get('modifier'):
                info = {name: row[name] for name in ('modifier', 'parameter', 'value', 'operation')}
                if key not in unique:
                    unique[key] = {**row, 'modifiers': [info]}
                else:
                    unique[key]['modifiers'].append(info)
            else:
                unique.setdefault(key, row)
        return list(unique.values())

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
                group = (row.get("effect", row["source"]), row.get("kind"))
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
