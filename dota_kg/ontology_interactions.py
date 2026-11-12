"""Evidence-backed effect candidates and explicit, conditional counter rules.

Rules are hypotheses for recommendations, not measured matchup advantages.
No closed-world assumption is made about missing effects or equipment.
"""
from rdflib import Graph, RDF, RDFS, URIRef

from .ontology import literal
from .ontology_schema import KG, RES, FEATURE_LABELS, bind


EFFECT_CODES = {
    "STUN", "ROOT", "SILENCE", "HEX", "LEASH", "DISARM", "FEAR", "TAUNT",
    "SLEEP", "BANISH", "KNOCKBACK", "SLOW", "CREATES_ILLUSIONS", "SUMMONS_UNITS",
    "ARMOR_REDUCTION", "MAGIC_RESIST_REDUCTION", "MANA_BURN", "MANA_DRAIN",
    "ANTI_HEAL", "INVISIBILITY", "EVASION", "TRUE_STRIKE", "HEAL", "LIFESTEAL",
    "SPELL_LIFESTEAL", "DAMAGE_OVER_TIME", "DAMAGE_REDUCTION", "DAMAGE_BLOCK",
    "ARMOR_BUFF", "MAGIC_RESISTANCE_BUFF", "DEBUFF_IMMUNITY", "INVULNERABILITY",
    "FORCED_MOVEMENT", "CHANNELING", "BREAK", "ANTI_ILLUSION", "AOE_DAMAGE",
    "BASIC_DISPEL", "STRONG_DISPEL", "HIGH_MOBILITY",
}

# These are reviewed item identities, not keyword matches over the entire catalog.
# A matching description is still required; absence never asserts the opposite.
ITEM_EFFECTS = {
    "item_gem": [("DETECTION", "True Sight")],
    "item_ward_sentry": [("DETECTION", "True Sight")],
    "item_dust": [("DETECTION", "reveals")],
    "item_sheepstick": [("HEX", "harmless"), ("ANTI_ILLUSION", "Instantly destroys illusions")],
    "item_orchid": [("SILENCE", "Silences")],
    "item_bloodthorn": [("SILENCE", "Silences")],
    "item_black_king_bar": [("DEBUFF_IMMUNITY", "any negative effect from enemy spells has no effect")],
    "item_skadi": [("ANTI_HEAL", "Health Restoration")],
    "item_assault": [("ARMOR_BUFF", "armor")],
    "item_bfury": [("CLEAVE_DAMAGE", "(Melee Only)")],
    "item_mjollnir": [("RETALIATORY_LIGHTNING", "Active: Static Charge")],
    "item_lotus_orb": [("ARMOR_BUFF", "Echo Shell")],
    "item_buckler": [("ARMOR_BUFF", "armor")],
    "item_monkey_king_bar": [("ACCURACY_PROC", "pierce through evasion")],
    "item_sphere": [("SPELL_BLOCK", "Blocks most targeted spells")],
}
ITEM_EFFECTS["item_mjollnir"].append(("CHAIN_LIGHTNING", "Passive: Chain Lightning"))

ABILITY_EFFECTS = {
    "drow_ranger_marksmanship": [("BASE_ARMOR_BYPASS", "ignoring their base armor")],
    "windrunner_shackleshot": [("STUN", "If no unit or tree is present")],
    "windrunner_focusfire": [("RAPID_ATTACKS", "Extra damage from secondary item effects is not reduced")],
    "drow_ranger_wave_of_silence": [("DETECTION", "reveals invisible")],
    "slardar_amplify_damage": [("DETECTION", "provides True Sight")],
    "bounty_hunter_track": [("DETECTION", "granting True Sight")],
    "zuus_lightning_bolt": [("DETECTION", "True Sight")],
    "zuus_thundergods_wrath": [("DETECTION", "True Sight")],
    "bloodseeker_thirst": [("DETECTION", "True Sight")],
}

# Valve's official tooltip renderer: 1=strong only, 2=yes, 3=no, 0=not specified.
# https://www.dota2.com/public/javascript/dota_react/main.js
DISPEL_CODES = {1: "Strong", 2: "Basic", 3: "None"}
DISPEL_EFFECTS = {"lion_impale": {"STUN"}, "lion_voodoo": {"HEX"},
                  "item_orchid": {"SILENCE"}, "item_bloodthorn": {"SILENCE"},
                  "silencer_global_silence": {"SILENCE"}, "drow_ranger_wave_of_silence": {"SILENCE"},
                  "ancient_apparition_ice_blast": {"ANTI_HEAL"}}
DISPEL_TARGETS = {"item_manta": "self", "item_lotus_orb": "ally",
                  "abaddon_aphotic_shield": "ally", "legion_commander_press_the_attack": "ally",
                  "oracle_false_promise": "ally", "slark_dark_pact": "self", "item_satanic": "self"}

# Authored purchase priorities, not restrictions on who can buy an item.
# (role, phase, priority, required attack type). Position may be overridden in queries.
ITEM_ROLE_PREFERENCES = {
    "item_bfury": [("carry", "mid", 3, "Melee")],
    "item_mjollnir": [("carry", "late", 3, None), ("core", "late", 3, None)],
    "item_sheepstick": [("support", "late", 3, None), ("core", "late", 2, None), ("carry", "late", 1, None)],
    "item_lotus_orb": [("support", "late", 3, None), ("core", "late", 2, None)],
    "item_buckler": [("support", "early", 3, None), ("core", "early", 2, None)],
    "item_assault": [("core", "late", 3, None), ("carry", "late", 2, None)],
}

# (source mechanic, target mechanic, qualification). All remain heuristic.
COUNTER_RULES = [
    ("ARMOR_BUFF", "LOW_ARMOR", "Повышение брони компенсирует низкую броню; проверить величину бонуса, стадию игры и условия применения."),
    ("STUN", "CHANNELING", "Контроль должен попасть во время применения; проверить невосприимчивость и исключения способности."),
    ("HEX", "CHANNELING", "Превращение должно примениться во время применения; проверить невосприимчивость."),
    ("SILENCE", "CHANNELING", "Проверить, прерывает ли безмолвие именно эту поддерживаемую способность."),
    ("DETECTION", "INVISIBILITY", "Цель должна попадать в область обнаружения; отдельные виды скрытия могут быть исключением."),
    ("ANTI_ILLUSION", "CREATES_ILLUSIONS", "Проверить тип иллюзии и ограничения конкретного средства."),
    ("AOE_DAMAGE", "CREATES_ILLUSIONS", "Урон по области — кандидат; достаточность урона и стойкость иллюзий не оценены."),
    ("AOE_DAMAGE", "SUMMONS_UNITS", "Проверить здоровье, сопротивления и особые правила призванного существа."),
    ("ANTI_HEAL", "HEAL", "Снижение лечения должно действовать на получателя лечения."),
    ("ANTI_HEAL", "LIFESTEAL", "Проверить применимость снижения восстановления к этому источнику вампиризма."),
    ("TRUE_STRIKE", "EVASION", "Проверить вероятность срабатывания и атаки, на которые распространяется эффект."),
    ("ROOT", "HIGH_MOBILITY", "Проверить, блокирует ли обездвиживание конкретную способность перемещения."),
    ("CLEAVE_DAMAGE", "CREATES_ILLUSIONS", "Требуются атаки ближнего боя; иллюзии должны попадать в область cleave."),
    ("RETALIATORY_LIGHTNING", "CREATES_ILLUSIONS", "Наложить Static Charge на атакуемую цель; молнии срабатывают с вероятностью при получении атак."),
    ("BASE_ARMOR_BYPASS", "HIGH_ARMOR", "Marksmanship procs ignore base armor, not bonus armor; keep enemy heroes outside the disable range."),
    ("EVASION", "MELEE", "Evasion can avoid physical attacks while active; accuracy effects can bypass it."),
    ("CLEAVE_DAMAGE", "SUMMONS_UNITS", "Melee attacks can cleave grouped summoned units; damage and unit durability still matter."),
    ("RETALIATORY_LIGHTNING", "SUMMONS_UNITS", "Apply Static Charge to the attacked unit; retaliation procs can hit nearby summons."),
    ("CHAIN_LIGHTNING", "CREATES_ILLUSIONS", "Chain Lightning procs can damage multiple illusions; it is not an instant-kill effect."),
]

NEW_CLASSES = {KG.Effect, KG.Mechanic, KG.CounterRule, KG.InteractionCondition, KG.HeroContext, KG.ItemRolePreference}
NEW_PROPERTIES = {KG.hasEffect, KG.effectMechanic, KG.derivedFromFeatureValue,
                  KG.requiresCondition, KG.blockedByCondition, KG.conditionScope,
                  KG.conditionKind, KG.conditionText, KG.conditionItem,
                  KG.conditionMechanic, KG.conditionTalent, KG.sourceMechanic,
                  KG.targetMechanic, KG.ruleStatus, KG.effectStatus,
                  KG.heldItem, KG.selectedTalent, KG.contextComplete, KG.requiredDispel,
                  KG.purchasePhase, KG.preferenceRank, KG.dispelTarget, KG.featureMechanic, KG.rulePurpose, KG.effectTarget, KG.mechanicCategory,
                  KG.ruleWeight, KG.ruleFamily, KG.sourceEffectTarget}


def build_interactions(game, rows=None):
    """Rebuild only the enrichment, using original entities and observed features."""
    graph = bind(Graph())

    def node(uri, cls, label):
        graph.add((uri, RDF.type, cls))
        graph.add((uri, RDFS.label, literal(label)))
        return uri

    def mechanic(code):
        return node(RES[f"mechanic/{code}"], KG.Mechanic,
                    FEATURE_LABELS.get(code, {"DETECTION": "Обнаружение невидимости",
                        "CLEAVE_DAMAGE": "Рассекающий урон от атак ближнего боя",
                        "RETALIATORY_LIGHTNING": "Ответные молнии при получении атак"}.get(code, code)))

    def condition(parent, code, kind, text, scope="target", item=None, target=None):
        uri = node(URIRef(str(parent) + "/condition/" + code), KG.InteractionCondition, text)
        for prop, value in ((KG.conditionKind, kind), (KG.conditionScope, scope), (KG.conditionText, text)):
            graph.add((uri, prop, literal(value)))
        if item is not None:
            graph.add((uri, KG.conditionItem, item))
        if target is not None:
            graph.add((uri, KG.conditionMechanic, mechanic(target)))
        return uri

    def effect(owner, code, evidence, status, observation=None):
        uri = node(URIRef(str(owner) + "/effect/" + code), KG.Effect,
                   f"{game.value(owner, RDFS.label)}: {FEATURE_LABELS.get(code, code)}")
        graph.add((owner, KG.hasEffect, uri))
        graph.add((uri, KG.effectMechanic, mechanic(code)))
        graph.add((uri, KG.evidence, literal(evidence)))
        graph.add((uri, KG.effectStatus, literal(status)))
        if observation is not None:
            graph.add((uri, KG.derivedFromFeatureValue, observation))
        name = str(game.value(owner, KG.internalName))
        code_value = game.value(owner, KG.dispellableCode)
        if code in DISPEL_EFFECTS.get(name, set()) and code_value is not None:
            required = DISPEL_CODES.get(code_value.toPython())
            if required is not None:
                graph.add((uri, KG.requiredDispel, literal(required)))
        if code == "CLEAVE_DAMAGE":
            graph.add((uri, KG.requiresCondition, condition(uri, "melee", "has-mechanic",
                "Cleave требует атак ближнего боя.", "source", target="MELEE")))
        if code == "RETALIATORY_LIGHTNING":
            graph.add((uri, KG.requiresCondition, condition(uri, "static-charge", "review",
                "Наложить Static Charge на атакуемую цель; ответные молнии срабатывают с вероятностью.", "source")))
        for prop, name in ((KG.grantedByScepter, "item_ultimate_scepter"),
                           (KG.grantedByShard, "item_aghanims_shard")):
            if game.value(owner, prop) == literal(True):
                items = list(game.subjects(KG.internalName, literal(name)))
                if len(items) != 1:
                    raise ValueError(f"Missing unique activation item: {name}")
                requirement = condition(uri, name, "has-item", f"Требуется {name}", "source", item=items[0])
                graph.add((uri, KG.requiresCondition, requirement))

    for observation in sorted(game.subjects(RDF.type, KG.FeatureValue), key=str):
        if game.value(observation, KG.isObserved) != literal(True):
            continue
        feature = game.value(observation, KG.feature)
        code = str(game.value(feature, KG.identifier))
        value = game.value(observation, KG.value)
        if code not in EFFECT_CODES or value is None or value.toPython() <= 0:
            continue
        for owner in game.objects(observation, KG.supportedByAbility):
            effect(owner, code, str(game.value(observation, KG.evidence)), "feature-derived", observation)

    for owner in sorted(game.subjects(RDF.type, KG.Item), key=str):
        name = str(game.value(owner, KG.internalName))
        description = str(game.value(owner, KG.description) or "")
        for code, phrase in ITEM_EFFECTS.get(name, []):
            if phrase.casefold() not in description.casefold():
                raise ValueError(f"Item effect needs review after description change: {name}/{code}")
            evidence = description
            if name == "item_lotus_orb" and code == "ARMOR_BUFF":
                armor = [str(v) for p in game.objects(owner, KG.hasParameter)
                         if str(game.value(p, KG.parameterName)) == "bonus_armor"
                         for n in game.objects(p, KG.hasBaseValue)
                         for v in game.objects(n, KG.numericValue)]
                if not armor:
                    raise ValueError("Lotus Orb armor bonus needs review")
                evidence += "; bonus_armor = " + ", ".join(armor)
            effect(owner, code, evidence, "curated-description")
        dispel = str(game.value(owner, KG.dispelType) or "")
        if dispel in {"Basic", "Strong"}:
            effect(owner, dispel.upper() + "_DISPEL", description, "structured-description")
        for role, phase, priority, attack in ITEM_ROLE_PREFERENCES.get(name, []):
            preference = node(URIRef(str(owner) + f"/preference/{role}/{phase}"), KG.ItemRolePreference,
                              f"{name}: {role}, {phase}")
            for prop, value in ((KG.item, owner), (KG.roleName, literal(role)),
                                (KG.purchasePhase, literal(phase)), (KG.preferenceRank, literal(priority)),
                                (KG.evidence, literal("Авторский приоритет покупки по роли, стадии и механике предмета."))):
                graph.add((preference, prop, value))
            if attack:
                graph.add((preference, KG.attackType, literal(attack)))

    for owner in sorted(game.subjects(RDF.type, KG.Ability), key=str):
        name = str(game.value(owner, KG.internalName))
        description = str(game.value(owner, KG.description) or "")
        for code, phrase in ABILITY_EFFECTS.get(name, []):
            if phrase.casefold() not in description.casefold():
                raise ValueError(f"Ability effect needs review after description change: {name}/{code}")
            effect(owner, code, description, "curated-description")
            uri = URIRef(str(owner) + "/effect/" + code)
            if code == "BASE_ARMOR_BYPASS":
                graph.add((uri, KG.requiresCondition, condition(uri, "positioning", "review",
                    "Learn Marksmanship and keep every enemy hero outside its disable range. Procs ignore base armor only.", "source")))
            if name == "windrunner_shackleshot":
                graph.add((uri, KG.requiresCondition, condition(uri, "shackle", "review",
                    "A unit or tree behind the target enables the full stun; otherwise only the short stun applies.", "target")))

    for owner, name in game.subject_objects(KG.internalName):
        if str(name) in DISPEL_TARGETS:
            graph.add((owner, KG.dispelTarget, literal(DISPEL_TARGETS[str(name)])))

    for source, target, qualification in COUNTER_RULES:
        rule = node(RES[f"counter-rule/{source}/{target}"], KG.CounterRule,
                    f"{source} против {target}")
        graph.add((rule, KG.sourceMechanic, mechanic(source)))
        graph.add((rule, KG.targetMechanic, mechanic(target)))
        graph.add((rule, KG.ruleStatus, literal("heuristic")))
        graph.add((rule, KG.rulePurpose, literal("compensate" if target == "LOW_ARMOR" else "counter")))
        graph.add((rule, KG.evidence, literal("Авторское правило подбора кандидатов; не статистическое доказательство преимущества.")))
        requirement = condition(rule, "applicability", "review", qualification)
        graph.add((rule, KG.requiresCondition, requirement))
        if source in {"STUN", "HEX", "SILENCE", "ROOT"}:
            blocker = condition(rule, "immunity", "has-mechanic",
                                "Невосприимчивость цели: проверить, проходит ли конкретный эффект сквозь неё.",
                                target="DEBUFF_IMMUNITY")
            graph.add((rule, KG.blockedByCondition, blocker))
    if rows:
        from .ontology_mechanics import build_gameplay_mechanics
        from .ontology_ranking import build_weighted_rules
        graph += build_gameplay_mechanics(game, rows, legacy=graph)
        graph += build_weighted_rules(graph, rows)
    for feature in game.subjects(RDF.type, KG.Feature):
        code = str(game.value(feature, KG.identifier))
        represented = RES[f"mechanic/{code}"]
        if (represented, RDF.type, KG.Mechanic) in graph:
            graph.add((feature, KG.featureMechanic, represented))
    return graph


def validate_interactions(game, rows=None):
    """Check exact reproducibility, including evidence and absence of extra effects."""
    expected = build_interactions(game, rows)
    subjects = {s for cls in NEW_CLASSES for s in game.subjects(RDF.type, cls)}
    actual = Graph()
    for triple in game:
        if triple[0] in subjects or triple[1] in NEW_PROPERTIES:
            actual.add(triple)
    if set(actual) != set(expected):
        raise ValueError("Interaction model differs from evidence-backed effects and conditional rules")
    return {cls: set(expected.subjects(RDF.type, cls)) for cls in NEW_CLASSES}
