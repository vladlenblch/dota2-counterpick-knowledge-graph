# Current dataset contract

All eight files are in `data/final/` as Parquet. IDs are canonical Valve IDs. Null means the source did not supply a defensible value. Arrays and original objects are JSON strings in Parquet to preserve level-specific values and unknown fields. Rates are fractions in `[0, 1]`; advantage is a signed fraction. The optional `retrieved_at` is a technical timestamp, not a patch or version key.

| File | One row | Main fields and types | Raw vs derived |
| --- | --- | --- | --- |
| `heroes.parquet` | One Valve listed hero | `hero_id` int, names string, primary attribute string, stats float, complexity int, facets/roles/raw JSON string | Valve `herodata`; attribute name decoded, all stats raw. |
| `abilities.parquet` | One named current ability attached to a hero | `ability_id`, `hero_id` int; names/description string; innate/ultimate bool; damage/behavior/target/immunity codes int/string; range, radius, duration, cooldown, costs, damage, special values JSON string; raw JSON string | Valve `herodata`; arrays and complete source object retained. No duplicate target or behavior code columns. |
| `items.parquet` | One named item from Valve `itemlist` with `itemdata` | `item_id`, cost, neutral tier int; names/description/effects string; costs and specials JSON string; purchase/neutral flags bool; raw JSON string | Valve. Active/passive text split from labeled HTML headings; `is_purchasable` approximates positive listed cost. |
| `hero_features.parquet` | One hero–feature relation | `hero_id` int, `feature` string, `value` float, `confidence` float, `derivation_method` string, `evidence` string | Derived from Valve structures, constrained description rules, numerical percentiles, and explicit manual overrides. |
| `hero_feature_matrix.parquet` | One hero | `hero_id` int; one float column per detected feature | Pivot of `hero_features`. A zero means *no extracted evidence* in this pipeline, not proof of absence. |
| `matchups.parquet` | One directional hero–opponent observation | `source` string, hero/opponent IDs int, matches/wins int, win rate and normalized advantage float, retrieved timestamp string | OpenDota games/wins. `normalized_advantage > 0` means the row hero performs better against the opponent than its aggregate baseline. |
| `hero_items.parquet` | One hero–item–phase observation | `source` string, hero/item IDs int, phase string, usage count int, retrieved timestamp string | OpenDota item counts by start/early/mid/late phase, mapped to Valve item IDs. Usage rates are unavailable without a denominator. |
| `hero_meta.parquet` | One hero aggregate observation | `source` string, hero ID int, matches/wins and professional counts int, win rate float, rank brackets and raw response JSON string, retrieved timestamp string | OpenDota rank bracket pick/win counts and professional pick/win/ban counts. |

## Advantage direction

`hero_id = A`, `opponent_hero_id = B` always means A's outcome against B. OpenDota reports A's `wins` and `games_played`; `win_rate = wins / games_played`, and `normalized_advantage = matchup_win_rate - A's aggregate heroStats win rate`. This compares two OpenDota aggregates that can cover different windows, so it is an approximate statistical signal. The original `win_rate` and counts remain intact. A positive result favors A.

## Feature confidence and review

`confidence = 1.0` is directly structured or computed from official stats; about `0.9` is a constrained text rule; `0.7–0.89` is a derived semantic rule requiring review. The reason is in `evidence`. `HIGH_ARMOR` uses the current Valve hero armor distribution's 75th percentile; `LOW_ARMOR` uses the 25th. `HIGH_MAGIC_RESISTANCE` also requires a value above the median to avoid labeling everyone when the common value is tied. `AOE_DAMAGE` needs damage evidence and either a positive radius special value or a specific multi-target phrase. `BKB_PIERCING_CONTROL` and `BKB_PIERCING_DAMAGE` remain separate; the provisional `immunity=3` interpretation is documented in `SOURCES.md`. `ANTI_ILLUSION` requires explicit illusion interaction or a reviewed manual reason, and `DEPENDS_ON_ILLUSIONS` requires a manual reason. The matrix is regenerated from the long table.

`data/reports/feature_coverage.json` lists extracted features for every hero; `feature_review.csv` gives a readable 127-hero semantic audit, and `low_confidence_features.json` lists derived relations. `ambiguous_mappings.json` records unresolved IDs/names; `unavailable_data.json` lists OpenDota field gaps. `validation.json` checks ID references, rates, wins, self matchups, and duplicate observation keys.
