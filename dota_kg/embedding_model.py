"""ComplEx scores and typed negative sampling for the embedding experiment."""

from collections import defaultdict

import numpy as np
import torch
from torch import nn


class ComplEx(nn.Module):
    def __init__(self, entity_count, relation_count, dimensions):
        super().__init__()
        self.entities = nn.Embedding(entity_count, 2 * dimensions)
        self.relations = nn.Embedding(relation_count, 2 * dimensions)
        nn.init.normal_(self.entities.weight, std=0.1)
        nn.init.normal_(self.relations.weight, std=0.1)

    def forward(self, triples):
        heads, relations, tails = triples.unbind(dim=1)
        h_re, h_im = self.entities(heads).chunk(2, dim=1)
        r_re, r_im = self.relations(relations).chunk(2, dim=1)
        t_re, t_im = self.entities(tails).chunk(2, dim=1)
        return ((h_re * r_re - h_im * r_im) * t_re +
                (h_re * r_im + h_im * r_re) * t_im).sum(dim=1)


def candidate_sets(triples):
    domains = defaultdict(set)
    positives = defaultdict(set)
    for h, r, t in triples:
        domains[r, 0].add(h)
        domains[r, 2].add(t)
        positives[r, 0, t].add(h)
        positives[r, 2, h].add(t)
    return {key: sorted(values) for key, values in domains.items()}, positives


def fit(model, triples, rng, epochs, *, weights, matchups=None):
    domains, positives = candidate_sets(triples)
    corruptions = [
        (np.array(sorted(set(domains[r, 0]) - positives[r, 0, t]), dtype=np.int64),
         np.array(sorted(set(domains[r, 2]) - positives[r, 2, h]), dtype=np.int64))
        for h, r, t in triples
    ]
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01, weight_decay=0.01)
    loss_fn = nn.BCEWithLogitsLoss(reduction="none")
    for _ in range(epochs):
        negatives = []
        for (h, r, t), (heads, tails) in zip(triples, corruptions, strict=True):
            if heads.size:
                negatives.extend((other, r, t) for other in rng.choice(heads, size=2))
            if tails.size:
                negatives.extend((h, r, other) for other in rng.choice(tails, size=2))
        batches = [triples, np.asarray(negatives, dtype=np.int64).reshape(-1, 3)]
        labels = [np.ones(len(batches[0])), np.zeros(len(batches[1]))]
        example_weights = [weights, np.ones(len(batches[1]))]
        if matchups is not None:
            pairs, outcomes = matchups
            batches.append(pairs)
            labels.append(outcomes)
            example_weights.append(np.ones(len(pairs)))
        edges = torch.from_numpy(np.concatenate(batches))
        targets = torch.from_numpy(np.concatenate(labels).astype(np.float32))
        loss_weights = torch.from_numpy(np.concatenate(example_weights).astype(np.float32))
        for indices in np.array_split(rng.permutation(len(edges)), (len(edges) + 511) // 512):
            optimizer.zero_grad()
            loss = (loss_fn(model(edges[indices]), targets[indices]) * loss_weights[indices]).mean()
            loss.backward()
            optimizer.step()


def average_rank(scores, correct_index):
    score = scores[correct_index]
    return float(np.count_nonzero(scores > score) + (1 + np.count_nonzero(scores == score)) / 2)


def rank_metrics(ranks):
    ranks = np.asarray(ranks)
    return {"queries": len(ranks), "MR": float(ranks.mean()), "MRR": float((1 / ranks).mean()),
            **{f"Hits@{k}": float((ranks <= k).mean()) for k in (1, 3, 10)}}


@torch.inference_mode()
def evaluate_structure(model, validation, full):
    domains, positives = candidate_sets(full)
    ranks = []
    for h, r, t in validation:
        for side, truth, fixed in ((0, h, t), (2, t, h)):
            candidates = [truth] + [e for e in domains[r, side] if e not in positives[r, side, fixed]]
            triples = np.tile([h, r, t], (len(candidates), 1))
            triples[:, side] = candidates
            ranks.append(average_rank(model(torch.from_numpy(triples)).numpy(), 0))
    return rank_metrics(ranks)


def hero_scores(entities, relation):
    """Score all directed hero pairs from arrays saved in the model archive."""
    h_re, h_im = np.split(entities, 2, axis=1)
    r_re, r_im = np.split(relation, 2)
    return ((h_re * r_re - h_im * r_im) @ h_re.T +
            (h_re * r_im + h_im * r_re) @ h_im.T)
