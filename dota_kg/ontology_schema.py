"""Canonical ontology readers and shared source-to-RDF predicate mappings."""
from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Namespace, RDF, RDFS, OWL, XSD

BASE = "https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/"
KG = Namespace(BASE + "ontology#")
RES = Namespace(BASE + "resource/")
PROV = Namespace("http://www.w3.org/ns/prov#")
SH = Namespace("http://www.w3.org/ns/shacl#")

TABLES = {
    "heroes": ("HeroSnapshot", ("hero_id",)),
    "abilities": ("AbilitySnapshot", ("ability_id",)),
    "items": ("ItemSnapshot", ("item_id",)),
    "hero_features": ("FeatureObservation", ("hero_id", "feature")),
    "hero_feature_matrix": ("FeatureVector", ("hero_id",)),
    "matchups": ("MatchupObservation", ("hero_id", "opponent_hero_id")),
    "hero_items": ("ItemUsageObservation", ("hero_id", "item_id", "phase")),
    "hero_meta": ("HeroStatisticsObservation", ("hero_id",)),
}

JSON_COLUMNS = {
    # raw is retained as the exact original cell string. Its full source tree is
    # already exported in the raw-archive modules, avoiding a second huge tree.
    "heroes": {"facets", "role_levels"},
    "abilities": {"notes", "cast_range", "cast_point", "radius", "duration", "cooldown",
                  "mana_cost", "health_cost", "damage", "special_values"},
    "items": {"bonuses", "cooldown", "mana_cost", "special_values"},
    "hero_meta": {"rank_brackets"},
}

FEATURE_LABELS = {
    "ALLY_DISPEL": "Развеивание на союзнике", "ANTI_HEAL": "Противодействие лечению",
    "ANTI_ILLUSION": "Противодействие иллюзиям", "ANTI_MOBILITY": "Ограничение мобильности",
    "AOE_ABILITY_COUNT": "Число способностей с уроном по области", "AOE_DAMAGE": "Урон по области",
    "ARMOR_BUFF": "Повышение брони", "ARMOR_PERCENTILE": "Процентиль брони",
    "ARMOR_REDUCTION": "Снижение брони", "ATTACK_RANGE": "Дальность атаки",
    "BANISH": "Временное выведение из боя", "BASE_ARMOR": "Исходная броня",
    "BASE_MAGIC_RESISTANCE": "Исходное сопротивление магии", "BASIC_DISPEL": "Обычное развеивание",
    "BKB_PIERCING_CONTROL": "Контроль сквозь невосприимчивость к эффектам",
    "BKB_PIERCING_DAMAGE": "Урон сквозь невосприимчивость к эффектам",
    "CHANNELING": "Поддерживаемая способность", "COOLDOWN_REDUCTION": "Сокращение перезарядки",
    "CREATES_ILLUSIONS": "Создание иллюзий", "DAMAGE_BLOCK": "Блокирование урона",
    "DAMAGE_OVER_TIME": "Периодический урон", "DAMAGE_REDUCTION": "Снижение входящего урона",
    "DASH": "Рывок", "DEBUFF_IMMUNITY": "Невосприимчивость к отрицательным эффектам",
    "DEPENDS_ON_ILLUSIONS": "Зависимость от иллюзий", "DISARM": "Запрет атак",
    "DISPEL_COUNT": "Число способностей с развеиванием", "EVASION": "Уклонение",
    "FEAR": "Страх", "FORCED_MOVEMENT": "Принудительное перемещение",
    "GLOBAL_ABILITY": "Глобальная способность", "HARD_CONTROL": "Сильный контроль",
    "HARD_CONTROL_COUNT": "Число способностей с сильным контролем", "HEAL": "Лечение",
    "HEAL_REDUCTION": "Снижение лечения", "HEX": "Превращение",
    "HIGH_ARMOR": "Высокая броня", "HIGH_MOBILITY": "Высокая мобильность",
    "INVISIBILITY": "Невидимость", "INVULNERABILITY": "Неуязвимость",
    "KNOCKBACK": "Отталкивание", "LEAP": "Прыжок", "LEASH": "Привязка",
    "LIFESTEAL": "Вампиризм от атак", "LONG_RANGE": "Большая дальность атаки",
    "LOW_ARMOR": "Низкая броня", "MAGICAL_DAMAGE": "Магический урон",
    "MAGIC_RESISTANCE_BUFF": "Повышение сопротивления магии", "MANA_BURN": "Выжигание маны",
    "MANA_DRAIN": "Вытягивание маны", "MAX_AOE_RADIUS": "Наибольший найденный радиус урона",
    "MAX_CONTROL_DURATION": "Наибольшая найденная длительность контроля", "MELEE": "Ближний бой",
    "MOBILITY_ABILITY_COUNT": "Число мобильных способностей", "MOVEMENT_SPEED": "Скорость передвижения",
    "PERCENT_HP_DAMAGE": "Урон в процентах от здоровья", "PHYSICAL_DAMAGE": "Физический урон",
    "PURE_DAMAGE": "Чистый урон", "RESET_MECHANIC": "Сброс перезарядки",
    "ROOT": "Обездвиживание", "ROOT_COUNT": "Число способностей с обездвиживанием",
    "SAVE": "Спасение союзника", "SELF_DISPEL": "Развеивание на себе",
    "SILENCE": "Безмолвие", "SILENCE_COUNT": "Число способностей с безмолвием",
    "SLEEP": "Сон", "SLOW": "Замедление", "SPELL_LIFESTEAL": "Вампиризм от способностей",
    "STRONG_DISPEL": "Сильное развеивание", "STUN": "Оглушение",
    "STUN_COUNT": "Число способностей с оглушением", "SUMMONER": "Герой с призывом",
    "SUMMONS_UNITS": "Призыв существ", "TAUNT": "Провокация", "TELEPORT": "Телепортация",
    "TRUE_STRIKE": "Атаки без промахов",
}
NUMERIC_FEATURES = {"ARMOR_PERCENTILE", "ATTACK_RANGE", "BASE_ARMOR", "BASE_MAGIC_RESISTANCE",
                    "MAX_AOE_RADIUS", "MAX_CONTROL_DURATION", "MOVEMENT_SPEED"}


SEMANTIC_FIELDS = {
    "value": KG.value, "matches": KG.matches, "wins": KG.wins, "win_rate": KG.winRate,
    "normalized_advantage": KG.normalizedAdvantage, "usage_count": KG.usageCount,
    "professional_matches": KG.professionalMatches, "professional_wins": KG.professionalWins,
    "professional_bans": KG.professionalBans, "evidence": KG.evidence,
}
DATA_GROUPS = {
    **dict.fromkeys(("heroes", "abilities", "items"), "entities"),
    **dict.fromkeys(("hero_features", "hero_feature_matrix"), "features"),
    **dict.fromkeys(("matchups", "hero_items", "hero_meta"), "statistics"),
}
SCHEMA_FILES = ("schema.ttl", "archive-schema.ttl")


def _camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part[:1].upper() + part[1:] for part in rest)


def column_predicate(table: str, name: str):
    """One canonical RDF predicate for each source column; no duplicate aliases."""
    if table == "hero_feature_matrix" and name != "hero_id":
        return KG.matrixValues
    special = {
        "feature": "featureCode", "source": "sourceCode", "retrieved_at": "retrievedAtText",
        "derivation_method": "method", "raw": "rawRecord",
    }
    if name in special:
        return KG[special[name]]
    if name in SEMANTIC_FIELDS:
        return SEMANTIC_FIELDS[name]
    if name in JSON_COLUMNS.get(table, set()):
        return KG[_camel(name) + "Text"]
    return KG[_camel(name)]


def structured_predicate(name: str):
    """Direct link from a record to a queryable, ordered JSON value."""
    return KG.specialValues if name == "special_values" else KG[_camel(name) + "Values"]


JSON_TEXT_PROPERTIES = {
    column_predicate(table, name)
    for table, names in JSON_COLUMNS.items() for name in names
} | {KG.rawRecord}
STRUCTURED_PROPERTIES = {
    structured_predicate(name) for names in JSON_COLUMNS.values() for name in names
}


def bind(graph: Graph) -> Graph:
    for prefix, ns in (("kg", KG), ("res", RES), ("prov", PROV),
                       ("owl", OWL), ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)):
        graph.bind(prefix, ns)
    return graph


def schema_graph(root=None, *, include_technical=False) -> Graph:
    """Read editable canonical TTL, optionally adding archive vocabulary.

    ``root`` is a project directory containing ``ontology/``. Schema definitions
    live only in those TTL files, so edits made in Protege survive data exports.
    Imports are resolved explicitly from bundled files, without network access.
    """
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    names = [SCHEMA_FILES[0]]
    if include_technical:
        names.append(SCHEMA_FILES[1])
    graph = bind(Graph())
    for name in names:
        graph.parse(root / "ontology" / name, format="turtle")
    return graph
