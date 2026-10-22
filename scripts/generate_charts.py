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
from dota_kg.ontology_schema import KG, RES, schema_graph

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
             ("НАБЛЮДЕНИЙ ПРИЗНАКОВ", len(set(graph.subjects(RDF.type, KG.FeatureObservation)))),
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


def ontology_relations(graph):
    selected = [
        ("snapshot", "Привязка к снимку данных"),
        ("hero", "Привязка к герою"),
        ("baselineStatistics", "Базовая статистика матчапа"),
        ("hasParameter", "Параметры способностей и предметов"),
        ("feature", "Назначение игрового признака"),
        ("supportedByAbility", "Способность как основание признака"),
        ("hasAbilitySnapshot", "Способности в снимке героя"),
    ]
    values = [sum(1 for _ in graph.triples((None, KG[predicate], None))) for predicate, _ in selected]
    labels = [f"{label}\n{predicate}" for predicate, label in selected]
    fig = figure("Какие отношения хранит граф", "Число RDF-утверждений по выбранным объектным свойствам игровой модели", (13, 6.5))
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
    """Draw an actual feature observation with its value and supporting ability."""
    import textwrap
    from matplotlib.patches import FancyArrowPatch

    hero = RES["hero/86"]
    feature = RES["feature/FORCED_MOVEMENT"]
    observations = [node for node in graph.subjects(KG.hero, hero)
                    if (node, RDF.type, KG.FeatureObservation) in graph
                    and (node, KG.feature, feature) in graph]
    if len(observations) != 1:
        raise ValueError("Expected one FORCED_MOVEMENT observation for Rubick")
    observation = observations[0]
    ability = graph.value(observation, KG.supportedByAbility)
    value = graph.value(observation, KG.value)
    evidence = graph.value(observation, KG.evidence)
    if ability is None or value is None or evidence is None:
        raise ValueError("Incomplete Rubick observation in the ontology")
    if (hero, KG.hasFeatureObservation, observation) not in graph:
        raise ValueError("Missing Rubick-to-observation link")
    name = str(graph.value(hero, RDFS.label))
    ability_name = str(graph.value(ability, RDFS.label))
    code = str(graph.value(feature, KG.identifier))

    fig = figure("Признак и его обоснование в графе",
                 "Реальный фрагмент RDF: герой, наблюдение, значение и подтверждающая способность", (13, 7.3))
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
    card(.375, .57, .225, .19, "FeatureObservation", "Наблюдение признака", title_size=11.5)
    card(.79, .67, .195, .18, "Feature", "\n".join(textwrap.wrap(str(graph.value(feature, RDFS.label)), width=19)),
         subtitle=code, title_size=11.3)
    card(.79, .36, .195, .18, "Ability", ability_name)
    card(.39, .30, .195, .15, "xsd:double", f"{float(value):g}",
         subtitle="Бинарный признак", title_size=19)
    arrow((.202, .665), (.367, .665), "hasFeatureObservation", (.284, .705))
    arrow((.608, .715), (.782, .75), "feature", (.696, .765))
    arrow((.608, .61), (.782, .465), "supportedByAbility", (.704, .567))
    arrow((.4875, .562), (.4875, .458), "value", (.522, .51))

    # Keep the literal English evidence, distinguishing it from Russian diagram labels.
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


def ontology_figures():
    directory = ROOT / "ontology"
    report = json.loads((directory / "validation.json").read_text(encoding="utf-8"))
    if not report["conforms"]:
        raise RuntimeError("Для графиков нужен успешный отчет проверки онтологии")
    for name, details in report["package_files"].items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != details["sha256"]:
            raise RuntimeError(f"Отчет проверки не соответствует файлу {name}")
    graph = load_ontology(directory, include_archive=False)
    ontology_overview(graph)
    ontology_relations(graph)
    ontology_example(graph)


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
