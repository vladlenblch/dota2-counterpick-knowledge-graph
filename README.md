# Dota 2 Counterpick Knowledge Graph — data preparation

This repository builds a **current snapshot** of Dota 2 entities, matchup observations, and evidence-backed hero mechanics. It does not build the knowledge graph or a counter score. A full rerun replaces the current raw snapshots and curated Parquet files; there is no patch history.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Collect and build

```bash
.venv/bin/python -m dota_kg collect valve
.venv/bin/python -m dota_kg collect opendota
.venv/bin/python -m dota_kg build
```

Valve and OpenDota collect independently under `data/raw/<source>/`. A failed collection preserves the previous complete snapshot of that source. `build` writes eight Parquet datasets under `data/final/` and validation reports under `data/reports/`.

Manual feature decisions belong in [`config/manual_overrides.json`](config/manual_overrides.json), each with `hero`, `feature`, `action` (`ADD` or `REMOVE`), and `reason`. The complete source and coverage limitations are in [`SOURCES.md`](SOURCES.md); dataset fields and semantics are in [`DATASET.md`](DATASET.md).
