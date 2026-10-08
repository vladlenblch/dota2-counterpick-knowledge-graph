"""Build README figures from the committed Parquet snapshot."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
FINAL = ROOT / "data" / "final"
CHARTS = ROOT / "assets" / "charts"
PREVIEW = os.environ.get("DOTA_KG_CHART_PREVIEW_DIR")

BG = "#F5F7FB"
WHITE = "#FFFFFF"
INK = "#17263C"
MUTED = "#617187"
GRID = "#DCE4ED"
BLUE = "#3869AE"
TEAL = "#1D9C92"
SKY = "#71B9CE"
AMBER = "#E8A846"
RED = "#D96565"

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
    fig = figure("Снимок данных Dota 2", f"Снимок API: {snapshot_date()}  ·  матчапы дополнены", (13, 5.6))
    cards = [
        ("ГЕРОИ", len(heroes), BLUE),
        ("СПОСОБНОСТИ", len(abilities), TEAL),
        ("ПРЕДМЕТЫ", len(items), SKY),
        ("ТИПЫ ПРИЗНАКОВ", len({row["feature"] for row in features}), AMBER),
    ]
    for index, (label, value, color) in enumerate(cards):
        x = .045 + index * .238
        fig.add_artist(FancyBboxPatch((x, .53), .218, .265, boxstyle="round,pad=0.008,rounding_size=0.025",
                                      transform=fig.transFigure, facecolor=WHITE, edgecolor=GRID, linewidth=.7))
        fig.text(x + .022, .735, label, fontsize=10, fontweight="bold", color=MUTED)
        fig.text(x + .022, .605, fmt(value), fontsize=31, fontweight="bold", color=color)
    fig.text(.045, .465, "Связи и наблюдения", fontsize=13, fontweight="bold")
    ax = fig.add_axes([.29, .12, .65, .31])
    names = ["Герой — противник", "Герой — предмет — стадия", "Герой — признак"]
    values = [len(matchups), len(hero_items), len(features)]
    colors = [BLUE, TEAL, AMBER]
    ax.barh([2, 1, 0], values, height=.53, color=colors, zorder=2)
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


def feature_patterns(heroes, features):
    fig = figure("Какие признаки извлечены", "По извлечённым связям «герой — признак»", (13, 5.8))
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

    excluded = ("BASE_", "MAX_", "ARMOR_PERCENTILE")
    common = Counter(row["feature"] for row in features
                     if not row["feature"].startswith(excluded)
                     and not row["feature"].endswith("_COUNT")
                     and row["feature"] not in ("MOVEMENT_SPEED", "ATTACK_RANGE"))
    top = common.most_common(10)
    labels = [FEATURE_NAMES.get(name, name.replace("_", " ").title()) for name, _ in top][::-1]
    values = [value for _, value in top][::-1]
    ax = fig.add_axes([.66, .17, .28, .57])
    ax.barh(range(len(top)), values, color=[TEAL if n in ("AOE_DAMAGE", "HARD_CONTROL") else BLUE for n, _ in top][::-1], height=.65, zorder=2)
    ax.set_yticks(range(len(top)), labels)
    ax.set_xlim(0, max(values) * 1.19)
    ax.set_xticks([])
    ax.set_title("Частые игровые свойства", loc="left", fontweight="bold", pad=16)
    clean_axis(ax)
    ax.grid(False)
    for index, value in enumerate(values):
        ax.text(value + 2, index, str(value), va="center", fontsize=9, fontweight="bold")
    save(fig, "features")


METHOD_NAMES = {
    "structured": "Структурированные поля Valve",
    "rule": "Правила по тексту",
    "count": "Счётчики способностей",
    "derived": "Производные правила",
    "structured+rule": "Поля + текст",
    "statistical": "Статистические пороги",
    "manual": "Ручная проверка",
}


def provenance(features):
    fig = figure("Откуда взялись признаки", "Способ получения каждой связи «герой — признак»", (12, 5.3))
    counts = Counter(row["derivation_method"] for row in features)
    entries = sorted(counts.items(), key=lambda item: item[1])
    ax = fig.add_axes([.33, .17, .60, .62])
    values = [value for _, value in entries]
    labels = [METHOD_NAMES.get(key, key) for key, _ in entries]
    colors = [AMBER if key == "manual" else TEAL if key in ("structured", "structured+rule") else BLUE for key, _ in entries]
    ax.barh(range(len(entries)), values, color=colors, height=.63, zorder=2)
    ax.set_yticks(range(len(entries)), labels)
    ax.set_xlim(0, max(values) * 1.16)
    ax.set_xticks([])
    clean_axis(ax)
    ax.grid(False)
    for index, value in enumerate(values):
        ax.text(value + 6, index, fmt(value), va="center", fontsize=10, fontweight="bold")
    save(fig, "provenance")


def matchup_coverage(heroes, matchups):
    total = len(heroes) * (len(heroes) - 1)
    observed = len({(row["hero_id"], row["opponent_hero_id"]) for row in matchups})
    names = {hero["hero_id"]: hero["display_name"] for hero in heroes}
    added = [row for row in matchups if row["source"] is None]
    added_pairs = {}
    for row in added:
        key = tuple(sorted((row["hero_id"], row["opponent_hero_id"])))
        added_pairs[key] = row["matches"]
    top = sorted(added_pairs.items(), key=lambda item: item[1], reverse=True)[:7]
    fig = figure("Покрытие противостояний", "Направленные пары героев в итоговой таблице", (13, 5.1))
    fig.text(.065, .67, f"{observed / total:.2%}".replace(".", ","), fontsize=43, color=BLUE, fontweight="bold")
    fig.text(.065, .55, f"{fmt(observed)} из {fmt(total)} возможных пар", fontsize=12, color=INK)
    fig.text(.065, .475, f"{fmt(len(added))} направления дополнены", fontsize=11, color=TEAL)
    ax = fig.add_axes([.065, .31, .42, .07])
    ax.barh([0], [total], color=GRID, height=.65)
    ax.barh([0], [observed], color=TEAL, height=.65)
    ax.set_xlim(0, total)
    ax.axis("off")

    if top:
        ax = fig.add_axes([.68, .17, .26, .58])
        top.reverse()
        ax.barh(range(len(top)), [count for _, count in top], color=BLUE, height=.63, zorder=2)
        ax.set_yticks(range(len(top)), [f"{names[a]} — {names[b]}" for (a, b), _ in top])
        ax.set_xlim(0, max(count for _, count in top) * 1.22)
        ax.set_xticks([])
        ax.set_title("Дополненные пары: число матчей", loc="left", fontsize=11, fontweight="bold", pad=14)
        clean_axis(ax)
        ax.grid(False)
        for index, (_, count) in enumerate(top):
            ax.text(count + max(value for _, value in top) * .016, index,
                    fmt(count), va="center", fontsize=9, fontweight="bold")
    save(fig, "matchups")


def item_phases(hero_items):
    counts = Counter(row["phase"] for row in hero_items)
    labels = [("start", "Старт"), ("early", "Ранняя"), ("mid", "Средняя"), ("late", "Поздняя")]
    values = [counts[key] for key, _ in labels]
    fig = figure("Предметы по стадиям игры", "Число связей «герой — предмет» в каждой стадии", (11, 4.8))
    ax = fig.add_axes([.09, .19, .78, .59])
    bars = ax.bar(range(4), values, color=[SKY, TEAL, BLUE, AMBER], width=.58, zorder=2)
    ax.set_xticks(range(4), [label for _, label in labels])
    ax.set_ylim(0, max(values) * 1.2)
    ax.set_ylabel("Число связей")
    clean_axis(ax, grid="y")
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value + max(values) * .025, fmt(value),
                ha="center", va="bottom", fontweight="bold", fontsize=11)
    fig.text(.91, .39, f"{fmt(len(hero_items))}\nвсего", fontsize=16, color=INK, fontweight="bold", ha="center")
    save(fig, "item_phases")


def main():
    heroes = table("heroes", ["hero_id", "display_name"])
    abilities = table("abilities", ["ability_id"])
    items = table("items", ["item_id"])
    features = table("hero_features", ["hero_id", "feature", "derivation_method"])
    matchups = table("matchups", ["hero_id", "opponent_hero_id", "source", "matches"])
    hero_items = table("hero_items", ["item_id", "phase"])
    overview(heroes, abilities, items, features, matchups, hero_items)
    feature_patterns(heroes, features)
    provenance(features)
    matchup_coverage(heroes, matchups)
    item_phases(hero_items)


if __name__ == "__main__":
    main()
