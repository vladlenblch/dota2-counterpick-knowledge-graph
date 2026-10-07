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

Each source collects independently under `data/raw/<source>/`. A failed collection preserves the previous complete snapshot of that source. `build` writes nine Parquet datasets under `data/final/`, an extra OpenDota hero meta table, and validation reports under `data/reports/`.

STRATZ requires a personal API token. Set `STRATZ_TOKEN` in your shell or put `STRATZ_TOKEN=...` in an ignored local `.env`, then run `.venv/bin/python -m dota_kg collect stratz` to discover the current GraphQL schema. In this environment STRATZ still returns HTTP 403, including a Cloudflare challenge. Statistics queries must be verified against an accessible schema before observations can be collected.

Direct automated Dotabuff requests return HTTP 403 here. Save public HTML pages under `data/raw/dotabuff/html/`. Hero pages can be named `phantom-lancer-counters.html` / `phantom-lancer-items.html` or saved in `phantom-lancer/counters.html` / `items.html`; the parser also reads the hero from the page heading. A general hero statistics page such as `heroes.html` is parsed into the extra `hero_meta.parquet`. Then rerun `build`. The pipeline makes no Dotabuff network requests.

Manual feature decisions belong in [`config/manual_overrides.json`](config/manual_overrides.json), each with `hero`, `feature`, `action` (`ADD` or `REMOVE`), and `reason`. The complete source and coverage limitations are in [`SOURCES.md`](SOURCES.md); dataset fields and semantics are in [`DATASET.md`](DATASET.md).
