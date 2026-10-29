"""Build README figures from the Parquet snapshot and its RDF ontology."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import pyarrow.parquet as pq
from rdflib import OWL, RDF, RDFS, URIRef

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dota_kg.ontology import load_ontology
from dota_kg.ontology_schema import KG, RES, ROLE_PROPERTIES, schema_graph

FINAL = ROOT / "data" / "final"
CHARTS = ROOT / "assets" / "charts"
PREVIEW = os.environ.get("DOTA_KG_CHART_PREVIEW_DIR")

BG = "#F5F7FB"
WHITE = "#FFFFFF"
INK = "#17263C"
MUTED = "#617187"
GRID = "#DCE4ED"
BLUE = "#3869AE"
AMBER = "#E8A846"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "text.color": INK,
    "axes.labelcolor": MUTED,
    "xtick.color": MUTED,
    "ytick.color": INK,
    "axes.edgecolor": GRID,
    "savefig.facecolor": BG,
    "svg.hashsalt": "dota-kg",
})


def fmt(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def table(name: str, columns: list[str] | None = None) -> list[dict]:
    return pq.read_table(FINAL / f"{name}.parquet", columns=columns).to_pylist()


def snapshot_date() -> str:
    months = ("", "января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря")
    dates = []
    for source in ("valve", "opendota"):
        manifest = ROOT / "data" / "raw" / source / "manifest.json"
        if manifest.exists():
            timestamp = json.loads(manifest.read_text(encoding="utf-8")).get("retrieved_at")
            if timestamp:
                dates.append(datetime.fromisoformat(timestamp).date())
    if not dates:
        return "дата снимка неизвестна"
    day = max(dates)
    return f"{day.day} {months[day.month]} {day.year}"


def figure(title: str, subtitle: str, size: tuple[int, int] = (13, 5)):
    fig = plt.figure(figsize=size, facecolor=BG)
    fig.text(.045, .94, title, fontsize=21, fontweight="bold", color=INK, va="top")
    fig.text(.045, .865, subtitle, fontsize=10.5, color=MUTED, va="top")
    return fig


def save(fig, name: str):
    CHARTS.mkdir(parents=True, exist_ok=True)
    path = CHARTS / f"{name}.svg"
    fig.savefig(path, bbox_inches="tight", pad_inches=.2, metadata={"Date": None})
    path.write_text("\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines()) + "\n",
                    encoding="utf-8")
    if PREVIEW:
        preview = Path(PREVIEW)
        preview.mkdir(parents=True, exist_ok=True)
        fig.savefig(preview / f"{name}.png", dpi=175, bbox_inches="tight", pad_inches=.2)
    plt.close(fig)


def clean_axis(ax, *, grid="x"):
    ax.set_facecolor(BG)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(axis=grid, color=GRID, linewidth=.8, zorder=0)
    ax.tick_params(length=0, pad=8)
    ax.set_axisbelow(True)


def overview(heroes, abilities, items, features, matchups, hero_items):
    fig = figure("Снимок данных Dota 2", f"Основной снимок API: {snapshot_date()}", (13, 5.6))
    cards = [
        ("ГЕРОИ", len(heroes)),
        ("СПОСОБНОСТИ", len(abilities)),
        ("ПРЕДМЕТЫ", len(items)),
        ("ТИПЫ ПРИЗНАКОВ", len({row["feature"] for row in features})),
    ]
    for index, (label, value) in enumerate(cards):
        x = .045 + index * .238
        fig.add_artist(FancyBboxPatch((x, .53), .218, .265, boxstyle="round,pad=0.008,rounding_size=0.025",
                                      transform=fig.transFigure, facecolor=WHITE, edgecolor=GRID, linewidth=.7))
        fig.text(x + .022, .735, label, fontsize=10, fontweight="bold", color=MUTED)
        fig.text(x + .022, .605, fmt(value), fontsize=31, fontweight="bold", color=BLUE)
    fig.text(.045, .465, "Связи и наблюдения", fontsize=13, fontweight="bold")
    ax = fig.add_axes([.29, .12, .65, .31])
    names = ["Герой - противник", "Герой - предмет - стадия", "Герой - признак"]
    values = [len(matchups), len(hero_items), len(features)]
    ax.barh([2, 1, 0], values, height=.53, color=BLUE, zorder=2)
    ax.set_yticks([2, 1, 0], names)
    ax.set_xlim(0, max(values) * 1.17)
    ax.set_xticks([])
    clean_axis(ax)
    ax.grid(False)
    for y, value in zip([2, 1, 0], values):
        ax.text(value + max(values) * .012, y, fmt(value), va="center", fontweight="bold", fontsize=11)
    save(fig, "overview")


FEATURE_NAMES = {
    "MAGICAL_DAMAGE": "Магический урон",
    "SLOW": "Замедление",
    "AOE_DAMAGE": "Урон по области",
    "HARD_CONTROL": "Жёсткий контроль",
    "MELEE": "Ближний бой",
    "STUN": "Оглушение",
    "DAMAGE_OVER_TIME": "Периодический урон",
    "HIGH_ARMOR": "Высокая броня",
    "HEAL": "Лечение",
    "BKB_PIERCING_DAMAGE": "Урон сквозь иммунитет",
}


def gameplay_feature(name: str) -> bool:
    return (not name.startswith(("BASE_", "MAX_", "ARMOR_PERCENTILE"))
            and not name.endswith("_COUNT")
            and name not in ("MOVEMENT_SPEED", "ATTACK_RANGE"))


def feature_patterns(heroes, features):
    fig = figure("Какие признаки извлечены", "По извлеченным связям героя с признаками", (13, 5.8))
    counts = Counter(row["hero_id"] for row in features)
    per_hero = [counts[hero["hero_id"]] for hero in heroes]
    ax = fig.add_axes([.065, .18, .34, .56])
    minimum, maximum = min(per_hero), max(per_hero)
    ax.hist(per_hero, bins=range(minimum, maximum + 2), color=BLUE, edgecolor=BG, linewidth=1, zorder=2)
    ax.axvline(median(per_hero), color=AMBER, linewidth=2, linestyle="--")
    ax.text(.98, .94, f"медиана: {median(per_hero):g}", transform=ax.transAxes,
            ha="right", va="top", color=INK, fontsize=10, fontweight="bold")
    ax.set_title("Признаков на героя", loc="left", fontweight="bold", pad=16)
    ax.set_xlabel("Число признаков")
    ax.set_ylabel("Число героев")
    ax.set_xticks(range(10, 31, 4))
    clean_axis(ax, grid="y")

    common = Counter(row["feature"] for row in features if gameplay_feature(row["feature"]))
    top = common.most_common(10)
    labels = [FEATURE_NAMES.get(name, name.replace("_", " ").title()) for name, _ in top][::-1]
    values = [value for _, value in top][::-1]
    ax = fig.add_axes([.66, .17, .28, .57])
    ax.barh(range(len(top)), values, color=BLUE, height=.65, zorder=2)
    ax.set_yticks(range(len(top)), labels)
    ax.set_xlim(0, max(values) * 1.19)
    ax.set_xticks([])
    ax.set_title("Частые игровые свойства", loc="left", fontweight="bold", pad=16)
    clean_axis(ax)
    ax.grid(False)
    for index, value in enumerate(values):
        ax.text(value + 2, index, str(value), va="center", fontsize=9, fontweight="bold")
    save(fig, "features")


def feature_pairs(features):
    fig = figure("Сочетания игровых признаков", "Сколько героев обладают обоими свойствами", (13, 6.4))
    common = Counter(row["feature"] for row in features if gameplay_feature(row["feature"]))
    selected = {name for name, _ in common.most_common(10)}
    by_hero = defaultdict(set)
    for row in features:
        if row["feature"] in selected:
            by_hero[row["hero_id"]].add(row["feature"])
    counts = Counter(pair for names in by_hero.values() for pair in combinations(sorted(names), 2))
    top = counts.most_common(10)[::-1]
    labels = [" + ".join(FEATURE_NAMES.get(name, name.replace("_", " ").title()) for name in pair)
              for pair, _ in top]
    values = [count for _, count in top]
    ax = fig.add_axes([.53, .12, .40, .65])
    ax.barh(range(len(top)), values, color=BLUE, height=.66, zorder=2)
    ax.set_yticks(range(len(top)), labels)
    ax.set_xlim(0, max(values) * 1.16)
    ax.set_xticks([])
    clean_axis(ax)
    ax.grid(False)
    for index, value in enumerate(values):
        ax.text(value + max(values) * .015, index, fmt(value), va="center", fontsize=10, fontweight="bold")
    save(fig, "feature_pairs")


def matchup_coverage(heroes, matchups):
    total = len(heroes) * (len(heroes) - 1)
    observed = len({(row["hero_id"], row["opponent_hero_id"]) for row in matchups})
    rates = [100 * row["win_rate"] for row in matchups if row["win_rate"] is not None]
    fig = figure("Противостояния героев", "Покрытие пар и распределение доли побед в итоговой таблице", (13, 5.1))
    fig.text(.065, .67, f"{observed / total:.2%}".replace(".", ","), fontsize=43, color=BLUE, fontweight="bold")
    fig.text(.065, .55, f"{fmt(observed)} из {fmt(total)} возможных пар", fontsize=12, color=INK)
    fig.text(.065, .46, "Все направленные пары героев", fontsize=11, color=BLUE)
    ax = fig.add_axes([.065, .31, .42, .07])
    ax.barh([0], [total], color=GRID, height=.65)
    ax.barh([0], [observed], color=BLUE, height=.65)
    ax.set_xlim(0, total)
    ax.axis("off")

    ax = fig.add_axes([.60, .20, .35, .56])
    ax.hist(rates, bins=range(0, 105, 5), color=BLUE, edgecolor=BG, linewidth=.8, zorder=2)
    ax.axvline(median(rates), color=AMBER, linewidth=2, linestyle="--")
    ax.text(.98, .94, f"медиана: {median(rates):.1f}%".replace(".", ","),
            transform=ax.transAxes, ha="right", va="top", fontsize=10, fontweight="bold")
    ax.set_title("Доля побед по направлениям", loc="left", fontsize=11, fontweight="bold", pad=14)
    ax.set_xlabel("Доля побед, %")
    ax.set_ylabel("Число пар")
    ax.set_xticks(range(0, 101, 20))
    clean_axis(ax, grid="y")
    save(fig, "matchups")


def item_phases(hero_items):
    counts = Counter(row["phase"] for row in hero_items)
    labels = [("start", "Старт"), ("early", "Ранняя"), ("mid", "Средняя"), ("late", "Поздняя")]
    values = [counts[key] for key, _ in labels]
    fig = figure("Предметы по стадиям игры", "Число связей героев с предметами в каждой стадии", (11, 4.8))
    ax = fig.add_axes([.09, .19, .78, .59])
    bars = ax.bar(range(4), values, color=BLUE, width=.58, zorder=2)
    ax.set_xticks(range(4), [label for _, label in labels])
    ax.set_ylim(0, max(values) * 1.2)
    ax.set_ylabel("Число связей")
    clean_axis(ax, grid="y")
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value + max(values) * .025, fmt(value),
                ha="center", va="bottom", fontweight="bold", fontsize=11)
    fig.text(.91, .39, f"{fmt(len(hero_items))}\nвсего", fontsize=16, color=INK, fontweight="bold", ha="center")
    save(fig, "item_phases")


def ontology_overview(graph):
    schema = schema_graph(ROOT)
    vocabulary = [len({node for node in schema.subjects(RDF.type, kind) if isinstance(node, URIRef)})
                  for kind in (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty)]
    fig = figure("Онтология в числах", "Структура OWL и факты игровой модели Dota 2", (13, 6.4))
    cards = [("RDF-ТРОЕК", len(graph)),
             ("ГЕРОЕВ", len(set(graph.subjects(RDF.type, KG.Hero)))),
             ("ЗНАЧЕНИЙ С ОБЪЯСНЕНИЕМ", sum(1 for _ in graph.triples((None, KG.evidence, None)))),
             ("ПАРАМЕТРОВ", len(set(graph.subjects(RDF.type, KG.Parameter))))]
    for index, (label, value) in enumerate(cards):
        x = .045 + index * .238
        fig.add_artist(FancyBboxPatch((x, .59), .218, .205,
                                      boxstyle="round,pad=0.008,rounding_size=0.025",
                                      transform=fig.transFigure, facecolor=WHITE, edgecolor=GRID, linewidth=.7))
        fig.text(x + .015, .75, label, fontsize=8.7, fontweight="bold", color=MUTED)
        fig.text(x + .015, .635, fmt(value), fontsize=27, fontweight="bold", color=BLUE)
    ax = fig.add_axes([.29, .12, .56, .36])
    y = [2, 1, 0]
    ax.barh(y, vocabulary, height=.54, color=BLUE)
    ax.set_yticks(y, ["Классы", "Объектные свойства\n(типы связей)", "Свойства данных\n(атрибуты)"])
    ax.set_xticks([])
    ax.set_xlim(0, max(vocabulary) * 1.15)
    clean_axis(ax)
    ax.grid(False)
    for position, value in zip(y, vocabulary):
        ax.text(value + max(vocabulary) * .025, position, fmt(value), va="center",
                fontsize=11, fontweight="bold")
    save(fig, "ontology_overview")


def ontology_hierarchy(graph):
    """Draw the actual class hierarchy from the editable OWL schema."""
    import textwrap

    roots = [(KG.Entity, "Игровые сущности"),
             (KG.GameCharacteristic, "Характеристики"),
             (KG.GameFact, "Факты о характеристиках"),
             (KG.Statistics, "Статистика")]
    order = {name: index for index, name in enumerate((
        "Hero", "Ability", "Item", "Talent", "Feature", "Parameter",
        "FeatureValue", "ParameterValue",
        "ParameterModifier", "GameNote", "HeroStatisticsObservation",
        "MatchupObservation", "ItemUsageObservation"))}
    hierarchy = defaultdict(list)
    for child, parent in graph.subject_objects(RDFS.subClassOf):
        if (child, RDF.type, OWL.Class) in graph:
            hierarchy[parent].append(child)

    def descendants(parent, depth=1):
        result = []
        for child in sorted(hierarchy[parent], key=lambda node: order.get(str(node).split("#")[-1], 99)):
            result.append((depth, child))
            result.extend(descendants(child, depth + 1))
        return result

    fig = figure("Иерархия предметной области", "Четыре ветви классов и их игровые понятия", (13, 6.8))
    ax = fig.add_axes([.025, .08, .95, .72])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    for column, (root, title) in enumerate(roots):
        x, width = .01 + column * .25, .23
        ax.add_patch(FancyBboxPatch((x, .04), width, .91,
                     boxstyle="round,pad=0.006,rounding_size=0.022",
                     facecolor=WHITE, edgecolor=GRID, linewidth=.8))
        ax.text(x + .015, .89, "\n".join(textwrap.wrap(title, width=19)),
                fontsize=11.4, fontweight="bold", color=BLUE, va="top", linespacing=1.3)
        ax.text(x + .015, .735, str(root).split("#")[-1], fontsize=8.5, color=MUTED)
        ax.plot([x + .015, x + width - .015], [.70, .70], color=GRID, linewidth=1)
        nodes = descendants(root)
        positions = [(.62 - index * .09, depth, node) for index, (depth, node) in enumerate(nodes)]
        first_level = [y for y, depth, _ in positions if depth == 1]
        if first_level:
            ax.plot([x + .018] * 2, [.68, min(first_level)], color=BLUE, linewidth=1.2)
        last_parent_y = None
        for y, depth, node in positions:
            start_x = x + .018 if depth == 1 else x + .038
            end_x = x + .04 + (depth - 1) * .018
            if depth > 1 and last_parent_y is not None:
                ax.plot([start_x] * 2, [last_parent_y - .025, y], color=BLUE, linewidth=1.1)
            ax.plot([start_x, end_x - .006], [y, y], color=BLUE, linewidth=1.1)
            label = str(graph.value(node, RDFS.label))
            ax.text(end_x, y, "\n".join(textwrap.wrap(label, width=25 if depth == 1 else 22)),
                    fontsize=9.4, color=INK, va="center", linespacing=1.12)
            if depth == 1:
                last_parent_y = y
    fig.text(.045, .045, f"Оценки {len(ROLE_PROPERTIES)} ролей заданы свойствами героя. Талант наследует свойства способности.",
             fontsize=9.5, color=MUTED)
    save(fig, "ontology_hierarchy")


def ontology_relations(graph):
    selected = [
        ("hero", "Привязка к герою"),
        ("opponent", "Противник в матчапе"),
        ("baselineStatistics", "Базовая статистика матчапа"),
        ("item", "Привязка к предмету"),
        ("hasParameter", "Игровые параметры"),
        ("feature", "Назначение игрового признака"),
        ("supportedByAbility", "Способность как основание признака"),
        ("hasAbility", "Способность героя"),
        ("hasFeatureValue", "Значение признака героя"),
        ("hasTalent", "Талант героя"),
        ("requiresTalent", "Талант как условие модификации"),
    ]
    counted = sorted(((predicate, label, sum(1 for _ in graph.triples((None, KG[predicate], None))))
                      for predicate, label in selected), key=lambda row: row[2], reverse=True)
    values = [value for _, _, value in counted]
    labels = [f"{label}\n{predicate}" for predicate, label, _ in counted]
    fig = figure("Какие отношения хранит граф", "Число RDF-утверждений по выбранным объектным свойствам игровой модели", (13, 8))
    ax = fig.add_axes([.44, .12, .48, .65])
    y = list(range(len(selected) - 1, -1, -1))
    ax.barh(y, values, height=.52, color=BLUE, zorder=2)
    ax.set_yticks(y, labels, fontsize=10)
    ax.set_xticks([])
    ax.set_xlim(0, max(values) * 1.16)
    clean_axis(ax)
    ax.grid(False)
    for position, value in zip(y, values):
        ax.text(value + max(values) * .015, position, fmt(value), va="center",
                fontsize=11, fontweight="bold")
    save(fig, "ontology_relations")


def ontology_example(graph):
    """Draw an actual unified hero feature with its value and supporting ability."""
    import textwrap
    from matplotlib.patches import FancyArrowPatch

    hero = RES["hero/86"]
    feature = RES["feature/FORCED_MOVEMENT"]
    observations = [node for node in graph.subjects(KG.hero, hero)
                    if (node, RDF.type, KG.FeatureValue) in graph
                    and (node, KG.feature, feature) in graph
                    and bool(graph.value(node, KG.isObserved))]
    if len(observations) != 1:
        raise ValueError("Expected one FORCED_MOVEMENT observation for Rubick")
    observation = observations[0]
    ability = graph.value(observation, KG.supportedByAbility)
    value = graph.value(observation, KG.value)
    evidence = graph.value(observation, KG.evidence)
    if ability is None or value is None or evidence is None:
        raise ValueError("Incomplete Rubick observation in the ontology")
    if (hero, KG.hasFeatureValue, observation) not in graph:
        raise ValueError("Missing Rubick-to-observation link")
    name = str(graph.value(hero, RDFS.label))
    ability_name = str(graph.value(ability, RDFS.label))
    code = str(graph.value(feature, KG.identifier))

    fig = figure("Признак и его обоснование в графе",
                 "Реальный фрагмент RDF: герой, признак, значение и объясняющая способность", (13, 7.3))
    ax = fig.add_axes([.035, .03, .93, .88])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    def card(x, y, width, height, kind, title, *, subtitle=None, accent=BLUE, title_size=13):
        ax.add_patch(FancyBboxPatch((x, y), width, height,
                     boxstyle="round,pad=0.008,rounding_size=0.018",
                     facecolor=WHITE, edgecolor=GRID, linewidth=.8, zorder=3))
        ax.text(x + .016, y + height - .033, kind, color=MUTED,
                fontsize=8.5, va="top", fontweight="bold", zorder=4)
        ax.text(x + .016, y + height * (.45 if subtitle else .38), title,
                color=accent, fontsize=title_size, fontweight="bold", va="center", zorder=4)
        if subtitle:
            ax.text(x + .016, y + .026, subtitle, color=MUTED,
                    fontsize=8.7, va="bottom", zorder=4)

    def arrow(start, end, label, text_position, *, curved=0):
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=13,
                     color=BLUE, linewidth=1.5,
                     connectionstyle=f"arc3,rad={curved}", zorder=2))
        ax.text(*text_position, label, color=MUTED, fontsize=8.4, ha="center", va="center",
                bbox={"facecolor": BG, "edgecolor": "none", "pad": 2}, zorder=5)

    card(.015, .57, .18, .19, "Hero", name)
    observation_label = str(graph.value(KG.FeatureValue, RDFS.label))
    card(.375, .57, .225, .19, "FeatureValue",
         "\n".join(textwrap.wrap(observation_label, width=22)), title_size=11.5)
    card(.79, .67, .195, .18, "Feature", "\n".join(textwrap.wrap(str(graph.value(feature, RDFS.label)), width=19)),
         subtitle=code, title_size=11.3)
    card(.79, .36, .195, .18, "Ability", ability_name)
    card(.39, .30, .195, .15, "xsd:double", f"{float(value):g}",
         subtitle="Бинарный признак", title_size=19)
    arrow((.202, .665), (.367, .665), "hasFeatureValue", (.284, .705))
    arrow((.608, .715), (.782, .75), "feature", (.696, .765))
    arrow((.608, .61), (.782, .465), "supportedByAbility", (.704, .567))
    arrow((.4875, .562), (.4875, .458), "value", (.522, .51))

    # Show the actual gameplay explanation alongside the Russian diagram labels.
    evidence_lines = "\n".join(textwrap.wrap(str(evidence), width=116))
    evidence_bottom = .015
    evidence_height = .20
    ax.add_patch(FancyBboxPatch((.055, evidence_bottom), .89, evidence_height,
                 boxstyle="round,pad=0.008,rounding_size=0.018", facecolor=WHITE,
                 edgecolor=GRID, linewidth=.8, zorder=3))
    ax.text(.075, evidence_bottom + evidence_height - .032, "evidence: исходное описание способности",
            fontsize=8.5, fontweight="bold", color=MUTED, va="top", zorder=4)
    ax.text(.075, evidence_bottom + evidence_height - .077, evidence_lines,
            fontsize=9.2, color=INK, va="top", linespacing=1.5, zorder=4)
    arrow((.377, .595), (.18, evidence_bottom + evidence_height + .008), "evidence", (.255, .39), curved=.13)
    save(fig, "ontology_example")



def ontology_graph(graph):
    """Collapse actual object assertions to class pairs; show all OWL classes."""
    import math
    import textwrap
    from matplotlib.path import Path as MplPath
    from matplotlib.patches import FancyArrowPatch

    schema = schema_graph(ROOT)
    classes = set(schema.subjects(RDF.type, OWL.Class))
    properties = set(schema.subjects(RDF.type, OWL.ObjectProperty))
    types = defaultdict(set)
    for node, kind in graph.subject_objects(RDF.type):
        if kind in classes:
            types[node].add(kind)
    pairs = defaultdict(set)
    for prop in properties:
        for subject, target in graph.subject_objects(prop):
            for source_class in types[subject]:
                for target_class in types[target]:
                    pairs[source_class, target_class].add(prop)

    roots = [KG.Entity, KG.GameFact, KG.GameCharacteristic, KG.Statistics]
    positions = {
        KG.Hero: (.14, .80), KG.Ability: (.14, .63), KG.Talent: (.14, .46), KG.Item: (.14, .29),
        KG.FeatureValue: (.50, .80), KG.GameNote: (.50, .63),
        KG.ParameterModifier: (.50, .46), KG.ParameterValue: (.50, .29),
        KG.Feature: (.86, .80), KG.Parameter: (.86, .51),
        KG.MatchupObservation: (.14, .10), KG.HeroStatisticsObservation: (.50, .10),
        KG.ItemUsageObservation: (.86, .10),
    }
    if classes != set(roots) | set(positions):
        raise ValueError("Update the ontology graph layout for the changed class vocabulary")
    if set().union(*pairs.values()) != properties:
        raise ValueError("Some object properties have no displayed class connections")

    bundles = {
        "A": pairs[KG.Ability, KG.Parameter] & pairs[KG.Item, KG.Parameter],
        "B": pairs[KG.HeroStatisticsObservation, KG.Parameter],
        "C": pairs[KG.Parameter, KG.ParameterValue],
    }
    local = lambda node: str(node).rsplit("#", 1)[-1]
    # Routes are presentation coordinates; endpoints and labels come from RDF.
    routes = {
        (KG.Hero, KG.Ability): ([(.025, .80), (.025, .63)], (.033, .716)),
        (KG.Ability, KG.Hero): ([(.26, .63), (.26, .80)], (.26, .716)),
        (KG.Hero, KG.Talent): ([(.012, .80), (.012, .46)], (.012, .55)),
        (KG.Talent, KG.Hero): ([(.285, .46), (.285, .80)], (.285, .755)),
        (KG.Hero, KG.FeatureValue): ([], (.32, .835)),
        (KG.FeatureValue, KG.Hero): ([(.50, .73), (.14, .73)], (.34, .737)),
        (KG.FeatureValue, KG.Feature): ([], (.68, .82)),
        (KG.FeatureValue, KG.Ability): ([(.365, .80), (.365, .68)], (.362, .72)),
        (KG.Ability, KG.GameNote): ([], (.32, .65)),
        (KG.Talent, KG.GameNote): ([(.32, .46), (.32, .59)], (.322, .562)),
        (KG.Item, KG.GameNote): ([(.345, .29), (.345, .60)], (.347, .39)),
        (KG.Ability, KG.Parameter): ([(.27, .56), (.70, .56)], (.58, .572)),
        (KG.Talent, KG.Parameter): ([(.25, .40), (.71, .40)], (.64, .407)),
        (KG.Item, KG.Parameter): ([(.25, .35), (.77, .35)], (.65, .36)),
        (KG.Parameter, KG.ParameterModifier): ([], (.687, .502)),
        (KG.Parameter, KG.ParameterValue): ([(.86, .29)], (.83, .36)),
        (KG.ParameterModifier, KG.ParameterValue): ([], (.555, .377)),
        (KG.ParameterModifier, KG.Talent): ([], (.323, .48)),
        (KG.Hero, KG.MatchupObservation): ([(-.038, .80), (-.038, .10)], (-.036, .335)),
        (KG.MatchupObservation, KG.Hero): ([(-.012, .10), (-.012, .87), (.14, .87)], (-.01, .40)),
        (KG.Hero, KG.HeroStatisticsObservation): ([(.26, .875), (.67, .875), (.67, .18), (.50, .18)], (.67, .65)),
        (KG.HeroStatisticsObservation, KG.Hero): ([(.50, .205), (-.064, .205), (-.064, .885), (.14, .885)], (-.064, .54)),
        (KG.Hero, KG.ItemUsageObservation): ([(.14, .955), (1.015, .955), (1.015, .10)], (1.017, .55)),
        (KG.ItemUsageObservation, KG.Hero): ([(.86, .01), (-.09, .01), (-.09, .90), (.14, .90)], (-.09, .66)),
        (KG.MatchupObservation, KG.HeroStatisticsObservation): ([], (.32, .12)),
        (KG.HeroStatisticsObservation, KG.Parameter): ([(.73, .10), (.73, .51)], (.73, .24)),
        (KG.Item, KG.ItemUsageObservation): ([(.14, .23), (.98, .23), (.98, .10)], (.79, .24)),
        (KG.ItemUsageObservation, KG.Item): ([(.86, .17), (.14, .17)], (.62, .18)),
    }
    if set(routes) != set(pairs):
        raise ValueError("Update the ontology graph routes for the changed class connections")

    fig = figure("Классы и связи онтологии",
                 f"{len(classes)} классов и {len(properties)} типов объектных свойств; связи данных свернуты до классов", (17, 15))
    ax = fig.add_axes([.07, .255, .87, .585])
    ax.set_xlim(-.115, 1.055)
    ax.set_ylim(-.025, .96)
    ax.axis("off")
    groups = [(KG.Entity, (.025, .24, .235, .70)),
              (KG.GameFact, (.385, .24, .23, .70)),
              (KG.GameCharacteristic, (.745, .24, .23, .70)),
              (KG.Statistics, (.025, .035, .95, .185))]
    for node, (cx, cy) in positions.items():
        ancestors = set(schema.transitive_objects(node, RDFS.subClassOf))
        parents = [(root, bounds) for root, bounds in groups if root in ancestors]
        if len(parents) != 1:
            raise ValueError(f"Unexpected class ancestry in the ontology graph: {node}")
        _, (x, y, group_width, group_height) = parents[0]
        if not (x <= cx <= x + group_width and y <= cy <= y + group_height):
            raise ValueError(f"Class drawn inside the wrong superclass: {node}")
    for root, (x, y, width, height) in groups:
        ax.add_patch(FancyBboxPatch((x, y), width, height,
                     boxstyle="round,pad=0.008,rounding_size=0.015",
                     facecolor="#EEF2F8", edgecolor=GRID, linewidth=.8, zorder=0))
        label = str(schema.value(root, RDFS.label))
        if root == KG.Statistics:
            ax.text(x + width - .016, y + height - .020, f"{label} / {local(root)}",
                    fontsize=10, fontweight="bold", color=BLUE, va="top", ha="right")
        else:
            ax.text(x + .012, y + height - .012, "\n".join(textwrap.wrap(label, width=26)),
                    fontsize=10, fontweight="bold", color=BLUE, va="top", linespacing=1.12)
            ax.text(x + .012, y + height - .077, local(root), fontsize=8.3, color=MUTED, va="top")

    width, height = .19, .078

    def boundary(node, toward):
        x, y = positions[node]
        dx, dy = toward[0] - x, toward[1] - y
        scale = min((width / 2 + .006) / abs(dx) if dx else math.inf,
                    (height / 2 + .006) / abs(dy) if dy else math.inf)
        return x + dx * scale, y + dy * scale

    def arrow(source, target, via, label, label_pos, *, dashed=False):
        points = [boundary(source, via[0] if via else positions[target]), *via,
                  boundary(target, via[-1] if via else positions[source])]
        path = MplPath(points, [MplPath.MOVETO] + [MplPath.LINETO] * (len(points) - 1))
        ax.add_patch(FancyArrowPatch(path=path, arrowstyle="-|>", mutation_scale=11,
                     color=MUTED if dashed else BLUE, linewidth=1 if dashed else 1.15,
                     linestyle="--" if dashed else "-", alpha=.85, zorder=1))
        ax.text(*label_pos, label, fontsize=8.5, color=MUTED if dashed else INK,
                ha="center", va="center", linespacing=1.2,
                bbox={"facecolor": BG, "edgecolor": "none", "pad": 2}, zorder=4,
                rotation=90 if label_pos[0] < .02 or label_pos[0] > 1 else 0)

    for (source, target), props in sorted(pairs.items(), key=lambda row: (str(row[0][0]), str(row[0][1]))):
        selected = set(props)
        labels = []
        for code, members in bundles.items():
            if members <= selected:
                labels.append(code)
                selected -= members
        labels += sorted(map(local, selected))
        via, label_pos = routes[source, target]
        arrow(source, target, via, "\n".join(labels), label_pos)
    for child, parent in schema.subject_objects(RDFS.subClassOf):
        if child in positions and parent in positions:
            x, y = positions[child]
            px, py = positions[parent]
            arrow(child, parent, [], "subClassOf", ((x + px) / 2 - .044, (y + py) / 2), dashed=True)

    for node, (x, y) in positions.items():
        ax.add_patch(FancyBboxPatch((x - width / 2, y - height / 2), width, height,
                     boxstyle="round,pad=0.004,rounding_size=0.012",
                     facecolor=WHITE, edgecolor=BLUE, linewidth=.85, zorder=3))
        label = "\n".join(textwrap.wrap(str(schema.value(node, RDFS.label)), width=25))
        ax.text(x, y + .009, label, fontsize=10.6, fontweight="bold", color=INK,
                ha="center", va="center", linespacing=1.12, zorder=4)
        ax.text(x, y - .027, local(node), fontsize=8.1, color=MUTED,
                ha="center", va="center", zorder=4)

    fig.text(.045, .225, "Синие стрелки - объектные связи. Рамки объединяют подклассы. Пунктир - наследование таланта от способности.",
             fontsize=9.2, color=MUTED)
    titles = {"A": "Параметры способностей и предметов", "B": "Параметры игровой статистики", "C": "Значения параметров"}
    for column, (code, members) in enumerate(bundles.items()):
        x = .045 + column * .315
        fig.add_artist(FancyBboxPatch((x, .065), .295, .132,
                       boxstyle="round,pad=0.009,rounding_size=0.012",
                       transform=fig.transFigure, facecolor=WHITE, edgecolor=GRID, linewidth=.8))
        fig.text(x + .012, .174, f"{code} / {titles[code]}", fontsize=10, fontweight="bold", color=BLUE)
        names = sorted(map(local, members))
        lines = ["  /  ".join(names[index:index + 2]) for index in range(0, len(names), 2)]
        fig.text(x + .012, .15, "\n".join(lines), fontsize=8.8, color=INK, va="top", linespacing=1.7)
    fig.text(.045, .033, "Показаны классы и связи между ними. Числовые и текстовые свойства не образуют связей между классами.",
             fontsize=9.2, color=MUTED)
    save(fig, "ontology_graph")


def ontology_figures():
    directory = ROOT / "ontology"
    report = json.loads((directory / "validation.json").read_text(encoding="utf-8"))
    if not report["conforms"]:
        raise RuntimeError("Для графиков нужен успешный отчет проверки онтологии")
    for name, details in report["package_files"].items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != details["sha256"]:
            raise RuntimeError(f"Отчет проверки не соответствует файлу {name}")
    graph = load_ontology(directory)
    ontology_overview(graph)
    ontology_hierarchy(graph)
    ontology_relations(graph)
    ontology_example(graph)
    ontology_graph(graph)


def main():
    heroes = table("heroes", ["hero_id"])
    abilities = table("abilities", ["ability_id"])
    items = table("items", ["item_id"])
    features = table("hero_features", ["hero_id", "feature"])
    matchups = table("matchups", ["hero_id", "opponent_hero_id", "win_rate"])
    hero_items = table("hero_items", ["phase"])
    overview(heroes, abilities, items, features, matchups, hero_items)
    feature_patterns(heroes, features)
    feature_pairs(features)
    matchup_coverage(heroes, matchups)
    item_phases(hero_items)
    ontology_figures()


if __name__ == "__main__":
    main()
