"""Train and demonstrate knowledge graph embeddings on the local Dota snapshot."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from rdflib import Graph, Literal, RDF, RDFS

from .common import ROOT
from .ontology import entity, load_ontology
from .ontology_schema import KG, RES, TABLE_PATHS

OUTPUT = ROOT / "data/reports/embeddings"
SEED = 42
MIN_MATCHES = 20
DIMENSIONS = 32
STRUCTURE_EPOCHS = 50
MATCHUP_EPOCHS = 45
ADVANTAGE = "hasAboveBaselineWinRateAgainst"
PREDICTIONS_FILE = "inferred-matchups.ttl"
PREDICTED_ADVANTAGE = KG.hasPredictedAboveBaselineWinRateAgainst


def project(graph, heroes):
    triples = {(hero, "hasAbility", ability) for hero in heroes
               for ability in graph.objects(hero, KG.hasAbility)}
    abilities = {ability for _, _, ability in triples}
    for owners, relation in ((abilities, "abilityHasMechanic"),
                             (graph.subjects(RDF.type, KG.Item), "itemHasMechanic")):
        for owner in owners:
            triples.update((owner, relation, mechanic)
                           for effect in graph.objects(owner, KG.hasEffect)
                           for mechanic in graph.objects(effect, KG.effectMechanic))
    triples.update((hero, "hasObservedFeature", feature) for hero in heroes
                   for node in graph.objects(hero, KG.hasFeatureValue)
                   if graph.value(node, KG.isObserved).toPython() and graph.value(node, KG.value).toPython() > 0
                   for feature in graph.objects(node, KG.feature))
    triples.update((feature, "featureMechanic", mechanic)
                   for feature, mechanic in graph.subject_objects(KG.featureMechanic))
    weighted = {(str(h), r, str(t)): 1.0 for h, r, t in triples}
    for rule, weight in graph.subject_objects(KG.ruleWeight):
        purpose = str(graph.value(rule, KG.rulePurpose))
        if purpose in {"counter", "purchase"}:
            source, target = graph.value(rule, KG.sourceMechanic), graph.value(rule, KG.targetMechanic)
            if source is not None and target is not None:
                relation = "countersMechanic" if purpose == "counter" else "purchaseCountersMechanic"
                weighted[str(source), relation, str(target)] = float(weight)
    return dict(sorted(weighted.items()))


def split_structure(triples, rng):
    entity_counts = Counter(e for h, _, t in triples for e in (h, t))
    relation_counts = Counter(triples[:, 1])
    holdout = np.zeros(len(triples), dtype=bool)
    uncovered = set(entity_counts)
    order = rng.permutation(len(triples))
    target = max(1, round(len(triples) * 0.15))
    added = 0
    for prefer_uncovered in (True, False):
        for index in order:
            h, r, t = triples[index]
            if added == target:
                break
            if (holdout[index] or min(entity_counts[h], entity_counts[t], relation_counts[r]) <= 1
                    or (prefer_uncovered and not ({h, t} & uncovered))):
                continue
            holdout[index] = True
            entity_counts[h] -= 1
            entity_counts[t] -= 1
            relation_counts[r] -= 1
            uncovered.difference_update((h, t))
            added += 1
    return triples[~holdout], triples[holdout]


def matchup_data(hero_index, rng):
    import pyarrow.parquet as pq

    columns = ["hero_id", "opponent_hero_id", "matches", "normalized_advantage", "retrieved_at"]
    rows = pq.read_table(ROOT / TABLE_PATHS["matchups"], columns=columns).to_pylist()
    observed = sorted((hero_index[row["hero_id"]], hero_index[row["opponent_hero_id"]],
                       row["normalized_advantage"], row["matches"])
                      for row in rows if row["normalized_advantage"] is not None
                      and math.isfinite(row["normalized_advantage"]) and row["matches"] >= MIN_MATCHES)
    pairs = np.array([(h, t) for h, t, _, _ in observed], dtype=np.int64)
    advantages = np.array([adv for _, _, adv, _ in observed])
    counts = np.array([matches for _, _, _, matches in observed])
    groups, row_to_group = np.unique(np.sort(pairs, axis=1), axis=0, return_inverse=True)
    order = rng.permutation(len(groups))
    train, valid, test = np.split(order, [int(0.70 * len(order)), int(0.85 * len(order))])
    eligible_groups = np.empty(len(groups), dtype=np.int64)
    for i, group in enumerate((train, valid, test)):
        eligible_groups[group] = i
    return pairs, advantages, counts, eligible_groups[row_to_group], sorted({
        row["retrieved_at"][:10] for row in rows if row["retrieved_at"]
    })


def clustering(embeddings, names, positions, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA
    from sklearn.metrics import adjusted_rand_score

    clusters = KMeans(n_clusters=5, n_init=30, random_state=SEED).fit_predict(embeddings) + 1
    pca = PCA(n_components=2)
    coords = pca.fit_transform(embeddings)
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.6), layout="constrained", sharex=True, sharey=True)
    for ax, labels, title, prefix in zip(axes, (positions, clusters),
                                        ("Основная игровая позиция", "Кластеры KMeans"),
                                        ("Позиция", "Кластер"), strict=True):
        for group in range(1, 6):
            selection = coords[labels == group]
            ax.scatter(*selection.T, s=29, alpha=0.83, label=f"{prefix} {group}")
        for name in ("Axe", "Phantom Lancer", "Crystal Maiden", "Lion"):
            if name in names:
                ax.annotate(name, coords[names.index(name)], xytext=(5, 4), textcoords="offset points", fontsize=8)
        ax.set(title=title, xlabel=f"PC1 ({pca.explained_variance_ratio_[0]:.1%})")
        ax.legend(loc="best", fontsize=8)
    axes[0].set_ylabel(f"PC2 ({pca.explained_variance_ratio_[1]:.1%})")
    fig.suptitle(f"Dota 2: векторные представления героев ({len(names)})")
    fig.savefig(output / "clusters.png", dpi=160)
    plt.close(fig)
    return {"adjusted_rand_index": float(adjusted_rand_score(positions, clusters)),
            "pca_explained_variance": pca.explained_variance_ratio_.tolist()}


def classify(embeddings, pairs, outcomes, split):
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
    from xgboost import XGBClassifier

    train, test = split == 0, split == 2
    baseline = int(outcomes[train].mean() > 0.5)
    predictions = {"majority_train": np.full(test.sum(), baseline)}
    for name, values in (("one_hot", np.eye(len(embeddings), dtype=np.float32)), ("embeddings", embeddings)):
        features = values[pairs].reshape(len(pairs), -1)
        classifier = XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.06, tree_method="hist",
                                   random_state=SEED, n_jobs=2)
        classifier.fit(features[train], outcomes[train])
        predictions[name] = classifier.predict(features[test])
    metrics = {name: {metric: float(function(outcomes[test], pred)) for metric, function in (
        ("accuracy", accuracy_score), ("balanced_accuracy", balanced_accuracy_score), ("f1", f1_score))}
               for name, pred in predictions.items()}
    return {"metrics": metrics, "majority_class_train": baseline,
            "majority_frequency_test": float(max(outcomes[test].mean(), 1 - outcomes[test].mean()))}


def evaluate_links(scores, pairs, is_positive, split):
    from .embedding_model import average_rank, rank_metrics

    hero_count = len(scores)
    train_heroes = pairs[split == 0, 0]
    frequency = np.bincount(train_heroes, weights=is_positive[split == 0], minlength=hero_count)
    frequency = (frequency + 1) / (np.bincount(train_heroes, minlength=hero_count) + 2)
    result = {}
    for subset, label in ((1, "validation"), (2, "test")):
        ranks, baseline_ranks, candidate_counts = [], [], []
        observed_negative = np.zeros_like(scores, dtype=bool)
        observed_negative[tuple(pairs[(split == subset) & ~is_positive].T)] = True
        for hero, enemy in pairs[(split == subset) & is_positive]:
            negatives = np.flatnonzero(observed_negative[:, enemy])
            if not len(negatives):
                continue
            candidates = np.r_[hero, negatives]
            ranks.append(average_rank(scores[candidates, enemy], 0))
            baseline_ranks.append(average_rank(frequency[candidates], 0))
            candidate_counts.append(len(candidates))
        result[label] = {"complex": rank_metrics(ranks), "train_hero_frequency": rank_metrics(baseline_ranks),
                         "mean_candidates": float(np.mean(candidate_counts))}
    return result


def demo_cases(scores, pairs, advantages, counts, split, names):
    cases = {}
    for target in ("Phantom Lancer", "Anti-Mage", "Crystal Maiden"):
        if target not in names:
            continue
        enemy = names.index(target)
        candidates = np.flatnonzero((split == 2) & (pairs[:, 1] == enemy))
        ordered = sorted(candidates, key=lambda i: (-scores[pairs[i, 0], enemy], names[pairs[i, 0]]))[:5]
        cases[target] = [{"hero": names[pairs[i, 0]], "score": float(scores[pairs[i, 0], enemy]),
                         "normalized_advantage": float(advantages[i]), "matches": int(counts[i])} for i in ordered]
    return cases


def train(output):
    import torch

    from .embedding_model import ComplEx, evaluate_structure, fit, hero_scores

    torch.set_num_threads(1)
    torch.manual_seed(SEED)
    rng = np.random.default_rng(SEED)
    graph = load_ontology(ROOT / "ontology", cache=True, progress=lambda line: print(line, flush=True))
    heroes = sorted(graph.subjects(RDF.type, KG.Hero), key=lambda h: graph.value(h, KG.identifier).toPython())
    names = [str(graph.value(h, RDFS.label)) for h in heroes]
    hero_ids = [int(graph.value(h, KG.identifier)) for h in heroes]
    positions = np.array([int(graph.value(h, KG.mostPopularPosition)) for h in heroes])
    projection = project(graph, heroes)
    rdf_triples = len(graph)
    entity_names = sorted({e for h, _, t in projection for e in (h, t)})
    relation_names = sorted({r for _, r, _ in projection} | {ADVANTAGE})
    entity_ids = {e: i for i, e in enumerate(entity_names)}
    relation_ids = {r: i for i, r in enumerate(relation_names)}
    triples = np.array([(entity_ids[h], relation_ids[r], entity_ids[t]) for h, r, t in projection], dtype=np.int64)
    weight_by_edge = {tuple(edge): weight for edge, weight in zip(triples, projection.values(), strict=True)}
    hero_nodes = np.array([entity_ids[str(h)] for h in heroes])
    del graph
    structural_train, structural_valid = split_structure(triples, rng)
    weights = np.array([weight_by_edge[tuple(edge)] for edge in structural_train])
    train_entities = set(structural_train[:, [0, 2]].ravel())
    if len(train_entities) != len(entity_names) or not len(structural_valid):
        raise ValueError("Структурное разбиение должно оставлять все сущности в обучении и тройки в валидации.")
    pairs, advantages, counts, split, dates = matchup_data({h: i for i, h in enumerate(hero_ids)}, rng)
    outcomes = (advantages > 0).astype(np.int64)
    if any(set(outcomes[split == i]) != {0, 1} for i in range(3)):
        raise ValueError("Для каждого разбиения матчапов требуются оба класса.")
    output.mkdir(parents=True, exist_ok=True)

    print(f"ComplEx: {len(structural_train)} обучающих троек, {len(structural_valid)} валидационных", flush=True)
    model = ComplEx(len(entity_names), len(relation_names), DIMENSIONS)
    fit(model, structural_train, rng, STRUCTURE_EPOCHS, weights=weights)
    structural_metrics = evaluate_structure(model, structural_valid, triples)
    structure_vectors = model.entities.weight.detach().numpy().copy()
    structural_relations = model.relations.weight.detach().numpy().copy()
    hero_vectors = structure_vectors[hero_nodes]
    print("Кластеризация и классификация", flush=True)
    clusters = clustering(hero_vectors, names, positions, output)
    classification = classify(hero_vectors, pairs, outcomes, split)

    print("Дообучение ComplEx на тренировочных матчапах", flush=True)
    train_pairs = pairs[split == 0]
    relation_index = relation_ids[ADVANTAGE]
    matchup_triples = np.column_stack((hero_nodes[train_pairs[:, 0]],
                                      np.full(len(train_pairs), relation_index), hero_nodes[train_pairs[:, 1]]))
    fit(model, structural_train, rng, MATCHUP_EPOCHS, weights=weights, matchups=(matchup_triples, outcomes[split == 0]))
    vectors = model.entities.weight.detach().numpy()
    relations = model.relations.weight.detach().numpy()
    scores = hero_scores(vectors[hero_nodes], relations[relation_index])
    if not np.isfinite(scores).all():
        raise ValueError("Модель вернула неконечные оценки матчапов.")
    np.savez_compressed(output / "model.npz", entity_ids=entity_names, relation_ids=relation_names,
                        hero_ids=hero_ids, hero_names=names, hero_indices=hero_nodes, positions=positions,
                        structural_vectors=structure_vectors, structural_relations=structural_relations,
                        vectors=vectors, relations=relations)
    report = {
        "seed": SEED, "data_dates": dates, "minimum_matches": MIN_MATCHES, "complex_dimensions": DIMENSIONS,
        "epochs": {"structural": STRUCTURE_EPOCHS, "matchup": MATCHUP_EPOCHS}, "heroes": len(heroes),
        "structure": {"rdf_triples": rdf_triples, "training_triples": len(structural_train),
                      "validation_triples": len(structural_valid),
                      "train_entities": len(train_entities),
                      "validation_entities": len(set(structural_valid[:, [0, 2]].ravel())),
                      "triples_by_relation": dict(Counter(r for _, r, _ in projection)),
                      "metrics": structural_metrics},
        "matchup_split": {name: {"pairs": int(sum(split == i)), "positive": int(sum(outcomes[split == i]))}
                          for i, name in enumerate(("train", "validation", "test"))},
        "clustering": clusters, "classification": classification,
        "link_prediction": evaluate_links(scores, pairs, outcomes.astype(bool), split),
        "test_examples": demo_cases(scores, pairs, advantages, counts, split, names),
    }
    (output / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    export_predictions(output)
    print(output / "metrics.json", flush=True)


def saved_scores(output):
    from .embedding_model import hero_scores

    with np.load(output / "model.npz") as model:
        relation = model["relation_ids"].tolist().index(ADVANTAGE)
        scores = hero_scores(model["vectors"][model["hero_indices"]], model["relations"][relation])
        return model["hero_ids"].tolist(), model["hero_names"].tolist(), scores


def export_predictions(output):
    import pyarrow.parquet as pq

    ids, _, scores = saved_scores(output)
    index = {hero_id: i for i, hero_id in enumerate(ids)}
    columns = ["hero_id", "opponent_hero_id", "source", "normalized_advantage"]
    rows = pq.read_table(ROOT / TABLE_PATHS["matchups"], columns=columns).to_pylist()
    predictions = Graph()
    predictions.bind("kg", KG)
    predictions.bind("res", RES)
    for row in rows:
        hero_id, enemy_id = row["hero_id"], row["opponent_hero_id"]
        if hero_id == enemy_id or row["source"] is not None or row["normalized_advantage"] is not None:
            continue
        score = float(scores[index[hero_id], index[enemy_id]])
        if score <= 0 or not math.isfinite(score):
            continue
        hero, enemy = entity("hero", hero_id), entity("hero", enemy_id)
        claim = RES[f"prediction/matchup/{hero_id}/{enemy_id}"]
        predictions.add((hero, PREDICTED_ADVANTAGE, enemy))
        predictions.add((claim, RDF.type, RDF.Statement))
        predictions.add((claim, RDF.subject, hero))
        predictions.add((claim, RDF.predicate, PREDICTED_ADVANTAGE))
        predictions.add((claim, RDF.object, enemy))
        predictions.add((claim, KG.predictionScore, Literal(score)))
        predictions.add((claim, KG.predictionModel, Literal("ComplEx")))
    turtle = predictions.serialize(format="turtle")
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, suffix=".ttl", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(turtle)
        temporary.replace(output / PREDICTIONS_FILE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    count = sum(1 for _ in predictions.subject_objects(PREDICTED_ADVANTAGE))
    print(f"Сохранено гипотез о матчапах: {count}")


def read_predictions(opponent, output=OUTPUT):
    graph = Graph().parse(output / PREDICTIONS_FILE, format="turtle")
    return sorted(((graph.value(claim, RDF.subject), float(graph.value(claim, KG.predictionScore)))
                   for claim in graph.subjects(RDF.object, opponent)
                   if graph.value(claim, RDF.predicate) == PREDICTED_ADVANTAGE),
                  key=lambda row: (-row[1], str(row[0])))


def rank(output, opponent, limit):
    _, names, scores = saved_scores(output)
    try:
        enemy = [name.casefold() for name in names].index(opponent.strip().casefold())
    except ValueError:
        raise ValueError(f"В модели нет героя {opponent!r}. Укажите полное английское имя.") from None
    ordered = sorted((i for i in range(len(names)) if i != enemy), key=lambda i: (-scores[i, enemy], names[i]))
    print(f"Баллы модели винрейта против {names[enemy]}")
    for position, i in enumerate(ordered[:limit], start=1):
        print(f"{position:>2}. {names[i]:<24} {scores[i, enemy]:+.3f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Каталог модели и отчёта")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("train", help="Обучить модель и запустить эксперименты на CPU")
    sub.add_parser("saturate", help="Сохранить RDF-гипотезы из обученной модели")
    ranking = sub.add_parser("rank", help="Показать баллы модели для винрейта выше среднего")
    ranking.add_argument("opponent")
    ranking.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    try:
        if args.command == "train":
            train(args.output)
        elif args.command == "saturate":
            export_predictions(args.output)
        else:
            if args.limit < 1:
                parser.error("--limit должен быть положительным")
            rank(args.output, args.opponent, args.limit)
    except (ValueError, FileNotFoundError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
