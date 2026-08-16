"""Mesure la qualité du retrieval sur le jeu de questions de référence."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api.rag import RAGIndex  # noqa: E402
from api.embeddings import embed_query  # noqa: E402
from api.query import build_retrieval_query  # noqa: E402
from core.config import get_settings  # noqa: E402


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Charge et valide les cas d'évaluation enregistrés au format JSONL."""
    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON invalide à la ligne {line_number}") from exc
            if not item.get("id") or not item.get("question"):
                raise ValueError(f"Cas incomplet à la ligne {line_number}")
            cases.append(item)
    return cases


def first_expected_rank(results: list[dict[str, Any]], pages: set[int]) -> int | None:
    """Retourne le rang de la première page attendue, ou aucun si elle est absente."""
    for rank, result in enumerate(results, start=1):
        if int(result["pdf_page"]) in pages:
            return rank
    return None


def evaluate(
    engine: RAGIndex,
    cases: list[dict[str, Any]],
    top_k: int,
    min_score: float,
    mode: str = "lexical",
    embedding_model: str = "text-embedding-3-small",
    embedding_cache_path: Path = PROJECT_ROOT / "storage" / "models",
    semantic_weight: float = 0.55,
    min_semantic_score: float = 0.32,
) -> dict[str, Any]:
    """Calcule les métriques de recherche, d'acceptation et de refus prudent."""
    details: list[dict[str, Any]] = []
    answerable_ranks: list[int | None] = []
    answerable_acceptance: list[bool] = []
    grounded_at_five: list[bool] = []
    refusal_checks: list[bool] = []
    by_difficulty: dict[str, list[bool]] = defaultdict(list)

    for case in cases:
        query = build_retrieval_query(case["question"])
        query_vector = (
            embed_query(query, model=embedding_model, cache_path=embedding_cache_path)
            if mode == "hybrid"
            else None
        )
        results = engine.retrieve(
            query,
            top_k=top_k,
            query_embedding=query_vector,
            semantic_weight=semantic_weight,
        )
        expected = {int(page) for page in case.get("expected_pages", [])}
        rank = first_expected_rank(results, expected) if expected else None
        relevant = engine.is_relevant(results, min_score, min_semantic_score)
        if expected:
            answerable_ranks.append(rank)
            answerable_acceptance.append(relevant)
            grounded_success = relevant and rank is not None and rank <= 5
            grounded_at_five.append(grounded_success)
            by_difficulty[case.get("difficulty", "unknown")].append(
                grounded_success
            )
            success = grounded_success
        else:
            correct_refusal = not relevant
            refusal_checks.append(correct_refusal)
            by_difficulty[case.get("difficulty", "unknown")].append(correct_refusal)
            success = correct_refusal

        details.append(
            {
                "id": case["id"],
                "question": case["question"],
                "difficulty": case.get("difficulty"),
                "expected_pages": sorted(expected),
                "first_expected_rank": rank,
                "relevant": relevant,
                "success_at_5_or_refusal": success,
                "top_results": [
                    {
                        "page": int(item["pdf_page"]),
                        "score": item["score"],
                        "chunk_id": int(item["chunk_id"]),
                    }
                    for item in results[:5]
                ],
            }
        )

    answerable_count = len(answerable_ranks)

    def hit_at(k: int) -> float:
        """Calcule la part des questions dont une page attendue apparaît avant k."""
        return sum(rank is not None and rank <= k for rank in answerable_ranks) / max(
            answerable_count, 1
        )

    reciprocal_ranks = [1.0 / rank if rank else 0.0 for rank in answerable_ranks]
    metrics = {
        "cases": len(cases),
        "answerable_cases": answerable_count,
        "unanswerable_cases": len(refusal_checks),
        "hit_at_1": round(hit_at(1), 4),
        "hit_at_3": round(hit_at(3), 4),
        "hit_at_5": round(hit_at(5), 4),
        "hit_at_12": round(hit_at(12), 4),
        "mrr": round(statistics.fmean(reciprocal_ranks), 4),
        "answerable_acceptance": round(
            sum(answerable_acceptance) / max(len(answerable_acceptance), 1), 4
        ),
        "grounded_hit_at_5": round(
            sum(grounded_at_five) / max(len(grounded_at_five), 1), 4
        ),
        "refusal_accuracy": round(
            sum(refusal_checks) / max(len(refusal_checks), 1), 4
        ),
        "success_by_difficulty": {
            name: round(sum(values) / len(values), 4)
            for name, values in sorted(by_difficulty.items())
        },
    }
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "index_metadata": engine.metadata,
        "metrics": metrics,
        "details": details,
    }


def main() -> int:
    """Analyse les arguments, exécute l'évaluation et écrit le rapport demandé."""
    parser = argparse.ArgumentParser(description="Évalue la récupération documentaire.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "evaluation" / "questions.jsonl",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--top-k", type=int, default=12)
    parser.add_argument("--mode", choices=("lexical", "hybrid"), default="lexical")
    args = parser.parse_args()

    settings = get_settings()
    engine = RAGIndex(settings.report_path, settings.index_path).load()
    if args.mode == "hybrid" and not engine.has_semantic_index:
        parser.error("L'index sémantique est absent. Lancez scripts/index_embeddings.py.")
    report = evaluate(
        engine,
        load_cases(args.dataset),
        top_k=max(args.top_k, 5),
        min_score=settings.min_relevance_score,
        mode=args.mode,
        embedding_model=settings.embedding_model,
        embedding_cache_path=settings.embedding_cache_path,
        semantic_weight=settings.semantic_weight,
        min_semantic_score=settings.min_semantic_score,
    )
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Rapport enregistré : {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
