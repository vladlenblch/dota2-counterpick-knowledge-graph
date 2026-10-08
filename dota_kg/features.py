"""Evidence-backed deterministic features from current Valve hero and ability data."""
from __future__ import annotations

import re
from collections import defaultdict

from .common import numeric

CONTROL_PATTERNS = {
    "STUN": r"\b(?:stuns|stunned|stunning|applies? (?:\w+ ){0,2}stun|causes? (?:\w+ ){0,2}stun|stun (?:to|on) (?:\w+ ){0,3}(?:enemy|target))\b",
    "ROOT": r"\b(?:roots?|rooted|rooting)\b",
    "SILENCE": r"\b(?:silences?|silenced|silencing|prevents? (?:\w+ ){0,4}cast(?:ing)? spells?)\b",
    "HEX": r"\b(?:hexes|hexed|hexing|hex|transforms? (?:\w+ ){0,4}into a harmless creature)\b",
    "LEASH": r"\b(?:leashes?|leashed|leashing)\b",
    "DISARM": r"\b(?:disarms?|disarmed|disarming)\b",
    "FEAR": r"\b(?:fears?|feared|causes? .*? to flee)\b",
    "TAUNT": r"\b(?:taunts?|taunted|taunting)\b",
    "SLEEP": r"\b(?:puts? .*? to sleep|falls? asleep|sleeping)\b",
    "BANISH": r"\b(?:banishes?|banished|banishing)\b",
    "KNOCKBACK": r"\b(?:knockback|knocks? .*? back)\b",
    "SLOW": r"\b(?:slows?|slowed|slowing)\b",
}
OTHER_PATTERNS = {
    "CREATES_ILLUSIONS": r"\b(?:creates?|creating|spawns?|spawning|summons?|summoning|generates?|generating) (?:\w+ ){0,4}illusions?\b|\billusions? (?:are|is) created\b",
    "ARMOR_REDUCTION": r"\b(?:reduces?|reducing|lowers?|decreases?|removes?) (?:\w+ ){0,4}armor\b|\barmor reduction\b",
    "MAGIC_RESIST_REDUCTION": r"\b(?:reduces?|reducing|lowers?|decreases?) (?:\w+ ){0,4}magic resistance\b",
    "MANA_BURN": r"\b(?:burns?|burned|burning) [^.]{0,45}\bmana\b|\bmana burn\b",
    "MANA_DRAIN": r"\b(?:drains?|drained|draining) (?:\w+ ){0,3}mana\b|\bmana drain\b",
    "ANTI_HEAL": r"\b(?:reduces?|reducing|prevents?|prevented from|stops?|blocks?) (?:\w+ ){0,4}(?:heal(?:ing|th)?|regenerat\w*)\b|\bheal(?:ing)? reduction\b|\bcannot (?:be )?heal(?:ed)?\b",
    "INVISIBILITY": r"\b(?:invisible|invisibility|becomes? unseen|out of visibility|shifts? out of visibility)\b",
    "EVASION": r"\b(?:evasion|evades? attacks?)\b",
    "TRUE_STRIKE": r"\btrue strike\b|\battacks? cannot miss\b",
    "BLINK": r"\b(?:blinks?|blinking)\b",
    "DASH": r"\b(?:dashes?|dashing|charges? into range|charging across the battlefield|rushes? to a target location)\b",
    "LEAP": r"\b(?:leaps?|leaping)\b",
    "TELEPORT": r"\b(?:teleports?|teleporting)\b",
    "HEAL": r"\b(?:heals?|healed|healing)\b",
    "LIFESTEAL": r"\b(?:lifesteal|life steal)\b",
    "SPELL_LIFESTEAL": r"\bspell lifesteal\b",
    "DAMAGE_OVER_TIME": r"\b(?:damage over time|damage every second|damage per second|damage each second)\b",
    "DAMAGE_REDUCTION": r"\b(?:reduces? (?:incoming|damage taken)|damage reduction)\b",
    "DAMAGE_BLOCK": r"\b(?:blocks? (?:incoming )?damage|damage block)\b",
    "ARMOR_BUFF": r"\b(?:gains?|grants?|provides?|increasing) (?:\w+ ){0,6}(?:bonus )?armor\b|\barmor (?:is )?increased\b",
    "MAGIC_RESISTANCE_BUFF": r"\b(?:grants?|increases?|provides?) [^.]{0,45}\bmagic resistance\b",
    "DEBUFF_IMMUNITY": r"\b(?:debuff immunity|immune to debuffs)\b",
    "INVULNERABILITY": r"\b(?:invulnerab(?:ility|le)|cannot be damaged)\b",
    "REFLECTS_DAMAGE": r"\b(?:reflects? damage|damage reflection)\b",
    "FORCED_MOVEMENT": r"\b(?:pushes? .*? away|pulls? .*? (?:towards?|into|to)|drags? .*? (?:towards?|into|to))\b",
    "PERCENT_HP_DAMAGE": r"\b(?:percentage|percent|%) of (?:\w+ ){0,4}(?:current|maximum|max) (?:health|hp)\b",
    "RESET_MECHANIC": r"\b(?:resets? (?:the )?cooldown|cooldown (?:is )?reset)\b",
    "COOLDOWN_REDUCTION": r"\bcooldown reduction\b|\breduces? (?:\w+ ){0,3}cooldowns?\b",
    "CHANNELING": r"\bchanneled\b|\bchanneling\b",
    "GLOBAL_ABILITY": r"\b(?:globally|global cast range|anywhere on the map)\b",
    "SUMMONS_UNITS": r"\b(?:summons?|spawns?|deploys?) (?:\w+ ){0,3}(?:unit|creep|treant|wolf|wolves|spiderling|golem|bear|familiar|eidolon|fragment|ward|turret)s?\b",
}
HARD = {"STUN", "HEX", "TAUNT", "SLEEP", "BANISH", "FEAR"}


def _percentile(value, distribution):
    return sum(x <= value for x in distribution) / len(distribution) if distribution else None


def _special_numbers(ability, token):
    out = []
    for special in ability.get("special_values") or []:
        if token in (special.get("name") or "").casefold():
            out += [n for v in (special.get("values_float") or []) if (n := numeric(v)) is not None]
    return out


def extract(heroes, abilities, manual_rows=()):
    by_hero = defaultdict(list)
    for ability in abilities:
        by_hero[ability["hero_id"]].append(ability)
    armors = sorted(x for hero in heroes if (x := numeric(hero.get("armor"))) is not None)
    resistances = sorted(x for hero in heroes if (x := numeric(hero.get("magic_resistance"))) is not None)
    features = []

    def emit(hid, feature, value, method, evidence):
        features.append({"hero_id": hid, "feature": feature, "value": float(value),
                         "derivation_method": method, "evidence": evidence[:500]})

    for hero in heroes:
        hid = hero["hero_id"]
        hero_abilities = by_hero[hid]
        numeric_base = {"BASE_ARMOR": "armor", "BASE_MAGIC_RESISTANCE": "magic_resistance",
                        "MOVEMENT_SPEED": "movement_speed", "ATTACK_RANGE": "attack_range"}
        for feature, field in numeric_base.items():
            value = numeric(hero.get(field))
            if value is not None:
                emit(hid, feature, value, "structured", f"Valve hero.{field} = {value}")
        armor = numeric(hero.get("armor"))
        if armor is not None:
            percentile = _percentile(armor, armors)
            emit(hid, "ARMOR_PERCENTILE", percentile, "statistical", f"Valve armor = {armor}; percentile among {len(armors)} heroes")
            if percentile >= 0.75:
                emit(hid, "HIGH_ARMOR", 1, "statistical", f"armor percentile = {percentile:.3f}")
            if percentile <= 0.25:
                emit(hid, "LOW_ARMOR", 1, "statistical", f"armor percentile = {percentile:.3f}")
        resistance = numeric(hero.get("magic_resistance"))
        if resistance is not None and resistances:
            percentile = _percentile(resistance, resistances)
            if percentile >= .75 and resistance > resistances[len(resistances) // 2]:
                emit(hid, "HIGH_MAGIC_RESISTANCE", 1, "statistical", f"magic resistance = {resistance}; percentile = {percentile:.3f}")
        if hero.get("attack_type") == "Melee":
            emit(hid, "MELEE", 1, "structured", "Valve attack_type = Melee")
        if (attack_range := numeric(hero.get("attack_range"))) is not None and attack_range >= 600:
            emit(hid, "LONG_RANGE", 1, "rule", f"Valve attack_range = {attack_range} >= 600")

        counts = defaultdict(int)
        max_radius = 0.0
        max_duration = 0.0
        for ability in hero_abilities:
            name = ability.get("display_name") or ability.get("internal_name") or str(ability["ability_id"])
            desc = ability.get("description") or ""
            full_text = desc.casefold()
            if not desc:
                continue
            evidence = f"Valve ability {name}: {desc}"
            found = set()
            for feature, pattern in {**CONTROL_PATTERNS, **OTHER_PATTERNS}.items():
                if re.search(pattern, full_text):
                    if feature == "HEAL" and re.search(OTHER_PATTERNS["ANTI_HEAL"], full_text):
                        continue
                    if feature in {"BLINK", "TELEPORT"} and (re.search(r"\b(?:prevent|prevents|preventing|cannot|stops?) [^.]{0,65}\b(?:blink|teleport)\w*", full_text) or re.search(r"\b(?:blink|teleport)\w* [^.]{0,30}\bwill break\b", full_text)):
                        continue
                    found.add(feature)
                    counts[feature] += 1
                    emit(hid, feature, 1, "rule", evidence)
            if re.search(r"\bsilence\b", name.casefold()) and re.search(r"\b(?:cast(?:ing)? spells?|abilities)\b", full_text) and "SILENCE" not in found:
                found.add("SILENCE")
                counts["SILENCE"] += 1
                emit(hid, "SILENCE", 1, "rule", evidence)
            if re.search(r"\bhex\b", name.casefold()) and "HEX" not in found:
                found.add("HEX")
                counts["HEX"] += 1
                emit(hid, "HEX", 1, "rule", evidence)
            if re.search(r"\b(?:preventing|prevents?|prevented) [^.]{0,100}casting spells\b", full_text) and "SILENCE" not in found:
                found.add("SILENCE")
                counts["SILENCE"] += 1
                emit(hid, "SILENCE", 1, "rule", evidence)
            if (re.search(r"\bmirror image\b", name.casefold()) and re.search(r"\bcreates? (?:\w+ ){0,3}images?\b", full_text)) or re.search(r"\b(?:copies|phantasms) [^.]{0,45}\billusions?\b", full_text):
                found.add("CREATES_ILLUSIONS")
                emit(hid, "CREATES_ILLUSIONS", 1, "rule", evidence)
            # BREAK must describe the Dota mechanic, not the ordinary verb.
            if re.search(r"\b(?:applies? break|break status|breaks? passives?|passives? (?:are|is) disabled)\b", full_text):
                found.add("BREAK")
                emit(hid, "BREAK", 1, "rule", evidence)
            if re.search(r"\b(?:destroys? illusions?|illusions? (?:are|is) instantly destroyed|bonus damage (?:to|against) illusions?)\b", full_text):
                emit(hid, "ANTI_ILLUSION", 1, "rule", evidence)
            if re.search(r"\b(?:prevents? blink|cannot blink|blink disabled|movement abilities? (?:are|is) disabled)\b", full_text) or found & {"ROOT", "LEASH"}:
                emit(hid, "ANTI_MOBILITY", 1, "derived", evidence)
            if found & HARD:
                counts["HARD_CONTROL"] += 1
                emit(hid, "HARD_CONTROL", 1, "derived", evidence)
                duration_values = [x for x in ability.get("duration") or [] if isinstance(x, (int, float))]
                for control in found & HARD:
                    duration_values += _special_numbers(ability, f"{control.casefold()}_duration")
                max_duration = max(max_duration, *duration_values, 0)
            if found & {"BLINK", "DASH", "LEAP", "TELEPORT"}:
                counts["MOBILITY_ABILITY_COUNT"] += 1
                emit(hid, "HIGH_MOBILITY", 1, "derived", evidence)
            elif re.search(r"\b(?:gaining max movement speed|moves? at maximum speed|moving increasingly fast|warps? backward)\b", full_text):
                counts["MOBILITY_ABILITY_COUNT"] += 1
                emit(hid, "HIGH_MOBILITY", 1, "derived", evidence)
            if found & {"SUMMONS_UNITS"}:
                emit(hid, "SUMMONER", 1, "derived", evidence)
            direct_hard_control = bool(re.search(r"\bcannot move,? attack,? or cast spells\b|\bdisabling their attacks and abilities\b|\btrapping all units caught\b|\bhero(?:es)? (?:are|is) forced to attack each other\b|\bhidden unit is invulnerable and disabled\b", full_text))
            if direct_hard_control:
                counts["HARD_CONTROL"] += 1
                emit(hid, "HARD_CONTROL", 1, "rule", evidence)
            if re.search(r"\bastral prison\b", full_text) and re.search(r"\bdisabled\b", full_text):
                emit(hid, "BANISH", 1, "rule", evidence)
            if re.search(r"\b(?:strong dispel|basic dispel)\b", full_text):
                if re.search(r"\b(?:self|himself|herself|itself)\b", full_text):
                    emit(hid, "SELF_DISPEL", 1, "derived", evidence)
                if re.search(r"\b(?:ally|allied|friendly)\b", full_text):
                    emit(hid, "ALLY_DISPEL", 1, "derived", evidence)
            if re.search(r"\bstrong dispel\b", full_text):
                counts["DISPEL_COUNT"] += 1
                emit(hid, "STRONG_DISPEL", 1, "rule", evidence)
            elif re.search(r"\bbasic dispel\b", full_text):
                counts["DISPEL_COUNT"] += 1
                emit(hid, "BASIC_DISPEL", 1, "rule", evidence)
            if "ANTI_HEAL" in found:
                emit(hid, "HEAL_REDUCTION", 1, "rule", evidence)
            if re.search(r"\b(?:allied?|friendly) (?:\w+ ){0,5}(?:invulnerable|invulnerability|physical damage|death|lethal damage)\b|\b(?:prevents? (?:an? )?ally .*? from dying|protects? an? ally from dying)\b", full_text):
                emit(hid, "SAVE", 1, "derived", evidence)
            damage_type = ability.get("damage_type_code")
            damage_feature = {1: "PHYSICAL_DAMAGE", 2: "MAGICAL_DAMAGE", 4: "PURE_DAMAGE"}.get(damage_type)
            has_damage = bool(damage_feature) and (any(numeric(x) and numeric(x) > 0 for x in ability.get("damage") or []) or re.search(r"\b(?:deals? (?:\w+ ){0,5}damage|dealing (?:\w+ ){0,5}damage|damages|damaging|inflicts? (?:\w+ ){0,5}damage|causes? (?:\w+ ){0,5}damage)\b", full_text))
            if damage_feature and has_damage:
                emit(hid, damage_feature, 1, "structured+rule", f"Valve ability {name} damage code = {damage_type}; {desc}")
            radius_values = [x for x in _special_numbers(ability, "radius") if x > 0]
            aoe_text = bool(re.search(r"\b(?:all nearby|nearby enem(?:y|ies)|multiple enemy units|all enem(?:y|ies)|each enemy|enemy units (?:along|in|within)|enemies (?:around|within|in an? area)|area around|area in front|target area|in a radius|in the aoe)\b", full_text))
            if (radius_values or aoe_text) and has_damage:
                if radius_values:
                    max_radius = max(max_radius, *radius_values)
                counts["AOE_ABILITY_COUNT"] += 1
                emit(hid, "AOE_DAMAGE", 1, "derived", f"{evidence}; radius = {max(radius_values) if radius_values else 'textual multi-target'}; damage code = {damage_type}")
            pierces = ability.get("immunity_code") == 3 or bool(re.search(r"\bpierces? debuff immunity\b|\bignores? debuff immunity\b", full_text))
            if pierces:
                if found & HARD or direct_hard_control:
                    emit(hid, "BKB_PIERCING_CONTROL", 1, "structured+rule", f"Valve ability {name} immunity code = {ability.get('immunity_code')}; {desc}")
                if has_damage:
                    emit(hid, "BKB_PIERCING_DAMAGE", 1, "structured+rule", f"Valve ability {name} immunity code = {ability.get('immunity_code')}; {desc}")
        for feature, count in {
            "AOE_ABILITY_COUNT": counts["AOE_ABILITY_COUNT"], "HARD_CONTROL_COUNT": counts["HARD_CONTROL"],
            "MOBILITY_ABILITY_COUNT": counts["MOBILITY_ABILITY_COUNT"], "STUN_COUNT": counts["STUN"],
            "ROOT_COUNT": counts["ROOT"], "SILENCE_COUNT": counts["SILENCE"],
            "DISPEL_COUNT": counts["DISPEL_COUNT"],
        }.items():
            emit(hid, feature, count, "count", f"Counted from {len(hero_abilities)} Valve abilities")
        if max_radius:
            emit(hid, "MAX_AOE_RADIUS", max_radius, "structured", "Maximum ability special_values radius")
        if max_duration:
            emit(hid, "MAX_CONTROL_DURATION", max_duration, "derived", "Maximum duration among detected hard control abilities")

    # Multiple abilities may support the same relation. Keep the first evidence in source order.
    best = {}
    for row in features:
        key = (row["hero_id"], row["feature"])
        best.setdefault(key, row)
    hero_ids = {hero["hero_id"] for hero in heroes}
    for row in manual_rows:
        if row["derivation_method"] != "manual" or row["hero_id"] not in hero_ids:
            raise ValueError(f"Invalid manual feature row: {row}")
        best[(row["hero_id"], row["feature"])] = row
    all_features = sorted({row["feature"] for row in best.values()})
    rows = sorted(
        (row for row in best.values() if not (row["feature"].endswith("_COUNT") and row["value"] == 0)),
        key=lambda x: (x["hero_id"], x["feature"]),
    )
    features_by_hero = defaultdict(dict)
    for row in rows:
        features_by_hero[row["hero_id"]][row["feature"]] = row["value"]
    matrix = [{"hero_id": hero["hero_id"], **{feature: features_by_hero[hero["hero_id"]].get(feature, 0.0) for feature in all_features}} for hero in heroes]
    return rows, matrix
