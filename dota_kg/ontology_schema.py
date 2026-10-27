"""Canonical game ontology and dataset-to-RDF predicate mappings."""
from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Namespace, RDF, RDFS, OWL, XSD

BASE = "https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/"
KG = Namespace(BASE + "ontology#")
RES = Namespace(BASE + "resource/")
SH = Namespace("http://www.w3.org/ns/shacl#")

TABLES = {
    "heroes": ("Hero", ("hero_id",)),
    "abilities": ("Ability", ("ability_id",)),
    "items": ("Item", ("item_id",)),
    "hero_features": ("FeatureValue", ("hero_id", "feature")),
    "hero_feature_matrix": ("FeatureValue", ("hero_id",)),
    "matchups": ("MatchupObservation", ("hero_id", "opponent_hero_id")),
    "hero_items": ("ItemUsageObservation", ("hero_id", "item_id", "phase")),
    "hero_meta": ("HeroStatisticsObservation", ("hero_id",)),
}

JSON_COLUMNS = {
    "heroes": {"facets", "role_levels"},
    "abilities": {"notes", "cast_range", "cast_point", "radius", "duration", "cooldown",
                  "mana_cost", "health_cost", "damage", "special_values"},
    "items": {"bonuses", "cooldown", "mana_cost", "special_values"},
    # Rank detail remains in the dataset; only aggregate statistics enter RDF.
    "hero_meta": {"rank_brackets"},
}

# The role_levels order matches Valve's role display, independently of lane positions:
# Carry, Support, Nuker, Disabler, Jungler, Durable, Escape, Pusher, Initiator.
ROLE_PROPERTIES = (
    KG.carryLevel,
    KG.supportLevel,
    KG.nukerLevel,
    KG.disablerLevel,
    KG.junglerLevel,
    KG.durableLevel,
    KG.escapeLevel,
    KG.pusherLevel,
    KG.initiatorLevel,
)

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
# These columns describe collection or implementation, not game facts.
NON_DOMAIN_COLUMNS = {"source", "retrieved_at", "derivation_method", "raw", "source_index"}
IDENTITY_COLUMNS = {"hero_id", "opponent_hero_id", "ability_id", "item_id", "feature", "display_name"}


def _camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part[:1].upper() + part[1:] for part in rest)


def column_predicate(table: str, name: str):
    """One canonical RDF predicate for each source column; no duplicate aliases."""
    if name in SEMANTIC_FIELDS:
        return SEMANTIC_FIELDS[name]
    return KG[_camel(name)]


def bind(graph: Graph) -> Graph:
    for prefix, ns in (("kg", KG), ("res", RES),
                       ("owl", OWL), ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)):
        graph.bind(prefix, ns)
    return graph


def schema_graph(root=None) -> Graph:
    """Read the editable OWL schema; local imports are handled by the loader."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return bind(Graph()).parse(root / "ontology/schema.ttl", format="turtle")


def evidence_text(value: str) -> str:
    """Keep the gameplay explanation without the collection brand prefix."""
    return value.replace("Valve ", "").replace("special_values", "parameter")
