"""Execute one text example per competency task on the same local graph."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota_kg.answers import KnowledgeBase, print_answer
from dota_kg.ontology import load_ontology


def check_queries(graph, root=ROOT, *, progress=None):
    examples = json.loads((Path(root) / "queries/examples.json").read_text(encoding="utf-8"))
    if sorted(e["cq"] for e in examples) != list(range(1, 13)):
        raise AssertionError("Нужен один пример для каждой из 12 задач")
    kb = KnowledgeBase(graph)
    results = {}
    for example in examples:
        result = kb.ask(example["question"])
        if result["cq"] != example["cq"] or not result["answers"]:
            raise AssertionError(f"Не получен ответ для CQ{example['cq']:02d}: {result}")
        if any(not row.get("answer") for row in result["answers"]):
            raise AssertionError("Пустой текст ответа")
        if progress:
            print("\nВопрос: " + example["question"], file=progress, flush=True)
            print_answer(result, progress)
        results[f"cq{example['cq']:02d}"] = {**result, "example_check_passed": True}
    # Validate relations independently of the generated prose.
    assert all(row.get("rule") and row.get("evidence") and row.get("target_facts")
               for row in results["cq01"]["answers"])
    assert all(row["required"] in ("Basic", "Strong", "None") for row in results["cq07"]["answers"])
    assert all(row["pierces"] is True for row in results["cq08"]["answers"])
    assert all(row["active"] is True for row in results["cq11"]["answers"])
    return results


def main():
    print("Загрузка онтологии...", flush=True)
    graph = load_ontology(ROOT / "ontology")
    results = check_queries(graph, progress=sys.stdout)
    directory = ROOT / "data/reports"
    directory.mkdir(parents=True, exist_ok=True)
    report = {"triples": len(graph), "scope": "12 text examples, not complete game coverage", "queries": results}
    (directory / "competency-queries.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Ответы графа на 12 вопросов", "", "Фактический вывод локального снимка; покрытие ограничено разметкой.", ""]
    for name, result in results.items():
        lines.extend([f"## {name}: {result['question']}", ""])
        lines.extend("- " + row["answer"] for row in result["answers"])
        lines.extend(["", *result["notes"], ""])
    (directory / "competency-answers.md").write_text("\n".join(lines), encoding="utf-8")
    print("\nПроверены 12 вопросов. Ответы: data/reports/competency-answers.md", flush=True)


if __name__ == "__main__":
    main()
