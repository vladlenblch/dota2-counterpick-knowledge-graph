"""Twelve competency tasks, answered by traversing the supplied RDF graph."""
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

    def value(self, node, prop, default=None):
        value = self.graph.value(node, prop)
        return default if value is None else value.toPython()

    def label(self, node):
        return str(self.graph.value(node, RDFS.label) or node)

    def is_item(self, node):
        return (node, RDF.type, KG.Item) in self.graph

    def effects(self, owner):
        return sorted(self.graph.objects(owner, KG.hasEffect), key=str)

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
                traits.setdefault(mechanic, []).append(fact)
        return traits

    def conditions(self, *nodes):
        result = []
        for node in nodes:
            for prop in (KG.requiresCondition, KG.blockedByCondition):
                for condition in sorted(self.graph.objects(node, prop), key=str):
                    if ((node, RDF.type, KG.CounterRule) in self.graph
                            and self.value(condition, KG.conditionKind) == "review"):
                        continue
                    text = self.value(condition, KG.conditionText)
                    if text:
                        result.append(text)
        return list(dict.fromkeys(result))

    def source_allowed(self, effect, hero=None):
        for condition in self.graph.objects(effect, KG.requiresCondition):
            if self.value(condition, KG.conditionScope) != "source":
                continue
            if self.value(condition, KG.conditionKind) == "has-mechanic" and hero:
                mechanic = self.graph.value(condition, KG.conditionMechanic)
                melee = mechanic == RES["mechanic/MELEE"] and self.value(hero, KG.attackType) == "Melee"
                if mechanic not in self.traits(hero) and not melee:
                    return False
        return True

    def candidates(self, targets, *, hero=None, items=False, subject=None, purpose="counter"):
        """Join source effects, graph rules and target facts; never bind an answer."""
        owners = ([subject] if subject else self.abilities(hero) if hero and not items else self.owners)
        for owner in owners:
            if self.is_item(owner) != items:
                continue
            if items and self.value(owner, KG.isPurchasable) is not True:
                continue
            for effect in self.effects(owner):
                if not self.source_allowed(effect, hero):
                    continue
                mechanic = self.graph.value(effect, KG.effectMechanic)
                for rule in self.rules:
                    if self.value(rule, KG.rulePurpose, "counter") != purpose:
                        continue
                    if self.graph.value(rule, KG.sourceMechanic) != mechanic:
                        continue
                    target = self.graph.value(rule, KG.targetMechanic)
                    if target not in targets:
                        continue
                    yield owner, effect, rule, targets[target]

    def candidate_row(self, owner, effect, rule, targets, **extra):
        source = self.graph.value(effect, KG.effectMechanic)
        target = self.graph.value(rule, KG.targetMechanic)
        conditions = self.conditions(effect, rule, *targets)
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

    def matchup_rate(self, hero, enemy):
        for observation in self.graph.objects(hero, KG.hasMatchup):
            if self.graph.value(observation, KG.opponent) == enemy:
                return self.value(observation, KG.winRate)
        return None

    def roles(self, hero):
        positions = {self.value(hero, predicate) for predicate in
                     (KG.mostPopularPosition, KG.secondMostPopularPosition)}
        return [role for role in ROLES if role in positions]

    def cq01(self, q):
        rows = []
        rates = {}
        for hero in self.heroes:
            if hero == q.enemy or q.hero and hero != q.hero:
                continue
            roles = self.roles(hero)
            if q.role:
                roles = [q.role] if q.role in roles else []
            rates[str(hero)] = self.matchup_rate(hero, q.enemy)
            for owner, effect, rule, facts in self.candidates(self.traits(q.enemy), hero=hero):
                for role in roles:
                    row = self.candidate_row(owner, effect, rule, facts, hero=str(hero), role=role)
                    row["answer"] = f"{ROLES[role]} против {self.label(q.enemy)}: {row['answer']}"
                    rows.append(row)
        return sorted(rows, key=lambda r: (r["role"],
                      -(rates[r["hero"]] if rates[r["hero"]] is not None else -1), r["hero"], r["source"]))

    def cq02(self, q):
        return [self.candidate_row(*candidate, hero=str(q.hero), enemy=str(q.enemy))
                for candidate in self.candidates(self.traits(q.enemy), hero=q.hero, subject=q.subject)]

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
            values = "; ".join(f"{p['name']}={p['values']}" for p in parameters if p["values"])
            if values:
                text = (text + " Параметры: " + values).strip()
            if not text:
                continue
            rows.append({"source": str(owner), "conditions": details, "parameters": parameters,
                         "answer": f"{self.label(owner)}: {text}"})
        return rows

    def mechanic_task(self, q, codes):
        targets = {RES[f"mechanic/{code}"]: [] for code in codes}
        if q.enemy or q.subject:
            if q.subject:
                known = {self.graph.value(e, KG.effectMechanic): [e] for e in self.effects(q.subject)}
            else:
                known = self.traits(q.enemy)
            targets = {m: known[m] for m in targets if m in known}
        rows = []
        for items in (False, True):
            for candidate in self.candidates(targets, hero=q.hero, items=items):
                rows.append(self.candidate_row(*candidate))
        return rows

    def cq04(self, q):
        return self.mechanic_task(q, ("CHANNELING",))

    def cq05(self, q):
        return self.mechanic_task(q, ("CREATES_ILLUSIONS", "SUMMONS_UNITS"))

    def cq06(self, q):
        return self.mechanic_task(q, ("INVISIBILITY",))

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
                    strength = {self.graph.value(e, KG.effectMechanic) for e in self.effects(cleanser)}
                    strong = RES["mechanic/STRONG_DISPEL"] in strength
                    if not strong and (required == "Strong" or RES["mechanic/BASIC_DISPEL"] not in strength):
                        continue
                    conditions = self.conditions(*self.effects(cleanser))
                    # A self dispel under hard disable cannot be assumed usable.
                    if required == "Strong" and scope == "self":
                        conditions.append("Проверить возможность применения под этим контролем; обычное нажатие предмета не гарантируется.")
                    rows.append({"source": str(owner), "effect": str(effect), "cleanser": str(cleanser),
                                 "required": required, "scope": scope, "conditions": conditions,
                                 "evidence": self.value(effect, KG.evidence),
                                 "answer": f"{self.label(owner)}: {self.label(cleanser)} — {'сильное' if strong else 'обычное'} развеивание ({'на себе' if scope == 'self' else 'на союзнике'}). " + " ".join(conditions)})
        return rows

    def cq08(self, q):
        owners = [q.subject] if q.subject else self.abilities(q.enemy) if q.enemy else (
            sorted(set(self.graph.subjects(RDF.type, KG.Ability)), key=str))
        return [{"ability": str(owner), "pierces": self.value(owner, KG.piercesDebuffImmunity) is True,
                 "answer": f"{self.label(owner)}: " + ("проходит сквозь невосприимчивость к эффектам."
                 if self.value(owner, KG.piercesDebuffImmunity) is True else
                 "явного прохода сквозь невосприимчивость в графе нет; по принятому правилу — не проходит.")}
                for owner in owners if q.subject or self.value(owner, KG.piercesDebuffImmunity) is True]

    def item_priority(self, item, q):
        role = {1: "carry", 2: "core", 3: "core", 4: "support", 5: "support"}.get(q.role)
        preferences = []
        phase_known = not q.phase
        for pref in self.graph.subjects(KG.item, item):
            if (pref, RDF.type, KG.ItemRolePreference) not in self.graph:
                continue
            if q.phase and self.value(pref, KG.purchasePhase) != q.phase:
                continue
            phase_known = True
            if role and self.value(pref, KG.roleName) != role:
                continue
            attack = self.value(pref, KG.attackType)
            if attack and attack != self.value(q.hero, KG.attackType):
                continue
            preferences.append(self.value(pref, KG.preferenceRank, 0))
        return max(preferences, default=0) if phase_known else None

    def item_rows(self, q, targets, purpose="counter"):
        rows = []
        for candidate in self.candidates(targets, hero=q.hero, items=True, subject=q.item, purpose=purpose):
            item = candidate[0]
            priority = self.item_priority(item, q)
            if priority is None:
                continue
            row = self.candidate_row(*candidate, item=str(item), priority=priority)
            row["answer"] = f"Для {self.label(q.hero)}: " + row["answer"]
            rows.append(row)
        return sorted(rows, key=lambda r: (-r["priority"], r["item"], r["rule"]))

    def cq09(self, q):
        return self.item_rows(q, self.traits(q.enemy))

    def cq10(self, q):
        return self.item_rows(q, self.traits(q.hero), purpose="compensate")

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
                            conclusion = "Предмет не активирован: этот блокирующий эффект не применяем."
                        elif q.active is None:
                            conclusion = "Одного владения предметом недостаточно: уточните, действует ли его эффект."
                        elif block == RES["mechanic/DEBUFF_IMMUNITY"] and pierces:
                            conclusion = "Есть явный проход сквозь невосприимчивость: этот блокирующий эффект не исключает способность."
                        else:
                            conclusion = "Активный эффект предмета ограничивает это противодействие; явного обхода в графе нет."
                        rows.append({"source": str(owner), "item": str(q.item), "rule": str(rule),
                                     "active": q.active, "pierces": pierces,
                                     "answer": f"{self.label(owner)} против {self.label(q.item)}: {conclusion} " + " ".join(self.conditions(effect, rule))})
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
                    detail = "; ".join(f"{p['name']}: база {p['base']}, ряд {kind} {p['upgraded']}" for p in parameters)
                    if detail:
                        detail += ". Итоговое значение не рассчитано: ряд улучшения может задавать модификатор."
                    rows.append({"ability": str(owner), "upgrade": kind, "evidence": text,
                                 "parameters": parameters, "grants_ability": granted is True,
                                 "answer": f"{self.label(owner)}, {kind}: {text or ''} {detail}".strip()})
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
                                 "answer": f"{self.label(owner)}: {name}; параметр {self.value(parameter, KG.parameterName)}, значение модификатора {value}, код операции {self.value(modifier, KG.operation)}."})
        return rows

    def ask(self, text, limit=3):
        if limit < 1:
            raise ValueError("Число вариантов должно быть положительным.")
        q = self.parser.parse(text)
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
        return {"question": text, "cq": q.cq,
                "parameters": {k: str(v) if hasattr(v, "n3") else v for k, v in vars(q).items() if v is not None},
                "answers": selected, "missing_roles": missing_roles,
                "status": "answered" if selected else "no_data", "notes": notes}


def print_answer(result, output):
    for row in result["answers"]:
        print("- " + row["answer"], file=output)
    for role in result["missing_roles"]:
        print(f"- {ROLES[role]}: в размеченных данных подходящих вариантов не найдено.", file=output)
    if not result["answers"] and not result["missing_roles"]:
        print("В размеченных данных подходящих вариантов не найдено.", file=output)
    for note in result["notes"]:
        print(note, file=output)
