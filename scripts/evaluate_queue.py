"""Evaluate deterministic question detection and cheap same-session fuzzy grouping."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from app.services.interactions import detect_candidate, normalize_text


def evaluate(path: Path) -> dict[str, float | int]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tp = fp = fn = 0
    clusters: list[dict[str, object]] = []
    assigned: list[tuple[int, str]] = []
    threshold = 0.78  # Keep aligned with interactions.SIMILARITY_THRESHOLD.
    for index, row in enumerate(rows):
        detected = detect_candidate(row["text"]) is not None
        expected = bool(row["is_question"])
        tp += detected and expected
        fp += detected and not expected
        fn += not detected and expected
        if not detected:
            continue
        normalized = normalize_text(row["text"])
        tokens = " ".join(sorted(normalized.split()))
        scores = []
        for cluster_index, cluster in enumerate(clusters):
            cluster_text = str(cluster["text"])
            cluster_tokens = " ".join(sorted(cluster_text.split()))
            scores.append((max(SequenceMatcher(None, normalized, cluster_text).ratio(),
                               SequenceMatcher(None, tokens, cluster_tokens).ratio()), cluster_index))
        match = max(scores, default=(0.0, -1))
        if match[0] >= threshold:
            cluster_index = match[1]
        else:
            cluster_index = len(clusters)
            clusters.append({"text": normalized, "gold": []})
        if row.get("group_id"):
            clusters[cluster_index]["gold"].append(str(row["group_id"]))
            assigned.append((cluster_index, str(row["group_id"])))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    gold_labels = [label for _, label in assigned]
    cluster_purity = sum(max(Counter(cluster["gold"]).values(), default=0) for cluster in clusters)
    purity = cluster_purity / len(gold_labels) if gold_labels else 0.0
    return {"messages": len(rows), "true_positive": tp, "false_positive": fp,
            "false_negative": fn, "question_precision": precision, "question_recall": recall,
            "detected_group_messages": len(gold_labels), "predicted_clusters": len(clusters),
            "grouping_purity": purity}


if __name__ == "__main__":
    result = evaluate(ROOT / "data" / "labeled_questions.jsonl")
    print(f"Messages: {result['messages']}")
    print(f"Question detection: precision={result['question_precision']:.3f} "
          f"recall={result['question_recall']:.3f} "
          f"(TP={result['true_positive']}, FP={result['false_positive']}, FN={result['false_negative']})")
    print(f"Cheap fuzzy grouping purity={result['grouping_purity']:.3f} "
          f"({result['detected_group_messages']} labeled questions across {result['predicted_clusters']} predicted clusters)")
