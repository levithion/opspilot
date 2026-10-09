"""Offline retrieval evaluation: hit@k, MRR and an access-control leakage check."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from opspilot.config import ROOT_DIR, get_settings
from opspilot.rag.retriever import Retriever
from opspilot.security.identity import Principal, normalise_roles


def _principal(role: str, team: str) -> Principal:
    return Principal(f"eval-{role}", f"{role}@eval.example", role, team, normalise_roles([role]), client_id="eval")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def evaluate(retriever: Retriever, qa: list[dict], leakage: list[dict], k: int = 4) -> dict[str, Any]:
    hits_at = {1: 0, 3: 0, k: 0}
    rr_total = 0.0
    failures = []
    for item in qa:
        p = _principal(item["role"], item["team"])
        docs = [h.doc_id for h in retriever.search(item["question"], p, k=k)]
        rank = docs.index(item["expected_doc"]) + 1 if item["expected_doc"] in docs else None
        for cutoff in hits_at:
            if rank is not None and rank <= cutoff:
                hits_at[cutoff] += 1
        rr_total += 1 / rank if rank else 0.0
        if rank is None or rank > 1:
            failures.append(
                {"id": item["id"], "question": item["question"], "expected": item["expected_doc"], "got": docs, "rank": rank}
            )
    leaks = []
    for item in leakage:
        p = _principal(item["role"], item["team"])
        docs = [h.doc_id for h in retriever.search(item["question"], p, k=k)]
        if item["forbidden_doc"] in docs:
            leaks.append({"id": item["id"], "question": item["question"], "docs": docs})
    n = len(qa)
    return {
        "questions": n,
        "hit_at_1": round(hits_at[1] / n, 4),
        "hit_at_3": round(hits_at[3] / n, 4),
        f"hit_at_{k}": round(hits_at[k] / n, 4),
        "mrr": round(rr_total / n, 4),
        "leakage_checks": len(leakage),
        "leaks": len(leaks),
        "leak_details": leaks,
        "misses_or_lower_rank": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality and access control")
    parser.add_argument("--qa", default=str(ROOT_DIR / "eval" / "qa.jsonl"))
    parser.add_argument("--leakage", default=str(ROOT_DIR / "eval" / "leakage.jsonl"))
    parser.add_argument("-k", type=int, default=4)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from opspilot.rag.ingest import build_store

    settings = get_settings()
    retriever = Retriever(build_store(settings), settings)
    report = evaluate(retriever, load_jsonl(Path(args.qa)), load_jsonl(Path(args.leakage)), k=args.k)
    if args.json:
        print(json.dumps(report, indent=2))
        return
    print(f"Questions:        {report['questions']}")
    for key in ("hit_at_1", "hit_at_3", f"hit_at_{args.k}", "mrr"):
        print(f"{key:<17} {report[key]:.1%}" if key != "mrr" else f"{key:<17} {report[key]:.3f}")
    print(f"Access leaks:     {report['leaks']} / {report['leakage_checks']}")
    for f in report["misses_or_lower_rank"]:
        print(f"  - {f['id']}: expected {f['expected']} rank={f['rank']} got={f['got']}")


if __name__ == "__main__":
    main()
