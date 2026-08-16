from pathlib import Path

import numpy as np
import pytest

from api.rag import RAGIndex
from api.providers import generate_extractive


ROOT = Path(__file__).resolve().parents[1]


def loaded_engine() -> RAGIndex:
    return RAGIndex(
        ROOT / "data" / "Rapport annuel 2025-BCM.pdf",
        ROOT / "storage" / "bcm_index.joblib",
    ).load()


def test_known_growth_fact_is_retrieved() -> None:
    results = loaded_engine().retrieve("Quel était le taux de croissance du PIB réel en 2025 ?")
    assert results
    assert any("4,0%" in item["text"].replace(" ", "") for item in results[:3])


def test_unrelated_question_is_rejected() -> None:
    engine = loaded_engine()
    results = engine.retrieve("Quelle est la recette traditionnelle des sushis japonais ?")
    assert not engine.is_relevant(results, 0.075)


def test_semantic_rank_can_recover_a_lexically_distant_passage() -> None:
    engine = loaded_engine()
    matrix = np.zeros((len(engine.chunks), 2), dtype=np.float32)
    matrix[:, 1] = 1.0
    target_index = next(
        index for index, chunk in enumerate(engine.chunks) if chunk.pdf_page == 110
    )
    matrix[target_index] = np.asarray([1.0, 0.0], dtype=np.float32)
    engine.semantic_matrix = matrix
    engine.embedding_model = "test-model"

    results = engine.retrieve(
        "formulation sans correspondance lexicale utile",
        top_k=5,
        query_embedding=np.asarray([1.0, 0.0], dtype=np.float32),
        semantic_weight=0.95,
    )
    assert results[0]["pdf_page"] == 110
    assert results[0]["retrieval_mode"] == "hybrid"
    assert results[0]["semantic_score"] == 1.0


def test_hybrid_relevance_requires_enough_semantic_evidence() -> None:
    weak = [{"score": 0.1, "lexical_score": 0.02, "semantic_score": 0.25, "keyword_overlap": 0}]
    strong = [{"score": 0.4, "lexical_score": 0.02, "semantic_score": 0.45, "keyword_overlap": 0}]
    assert not RAGIndex.is_relevant(weak, 0.075, 0.88)
    assert not RAGIndex.is_relevant(strong, 0.075, 0.88)


def test_query_expansion_uses_report_vocabulary() -> None:
    from api.query import build_retrieval_query

    query = build_retrieval_query(
        "Quelle était la taille du bilan agrégé des établissements bancaires ?"
    )
    assert "total des actifs secteur bancaire" in query
    assert query.endswith("Mauritanie")


def test_arabic_payment_query_is_expanded_with_the_report_vocabulary() -> None:
    from api.query import build_retrieval_query

    query = build_retrieval_query(
        "ما هي الإصلاحات التي يجري طرحها لأنظمة الدفع؟"
    )
    assert "réformes systèmes et moyens de paiement" in query
    assert "interopérabilité compensation règlement" in query
    assert not query.endswith("Mauritanie")


def test_neighbor_expansion_keeps_a_three_chunk_table_complete() -> None:
    engine = loaded_engine()
    chunk = next(
        item
        for item in engine.chunks
        if item.pdf_page == 113 and "Note 12 : Comptes courants" in item.text
    )
    result = {
        "chunk_id": chunk.chunk_id,
        "pdf_page": chunk.pdf_page,
        "text": chunk.text,
        "score": 0.45,
        "keyword_overlap": 4,
        "query_keyword_count": 8,
    }
    expanded = engine.expand_with_neighbors([result], max_results=6)
    assert any("13 001 546 726" in item["text"] for item in expanded)


@pytest.mark.parametrize(
    ("question", "expected_values"),
    [
        (
            "Comparer les dépôts de la clientèle entre 2024 et 2025",
            ("134,0", "158,7", "24,7"),
        ),
        (
            "Comparer les crédits à la clientèle entre 2024 et 2025",
            ("109,1", "122,8", "13,7"),
        ),
    ],
)
def test_extractive_answer_compares_any_matching_table_row(
    question: str, expected_values: tuple[str, str, str]
) -> None:
    engine = loaded_engine()
    chunk = next(
        item
        for item in engine.chunks
        if item.pdf_page == 33 and "Dépôts de la clientèle" in item.text
    )
    results = [
        {
            "chunk_id": chunk.chunk_id,
            "pdf_page": chunk.pdf_page,
            "text": chunk.text,
            "score": 0.5,
            "keyword_overlap": 5,
            "query_keyword_count": 8,
        }
    ]
    answer = generate_extractive(question, results).replace(" ", "")
    for value in expected_values:
        assert value in answer
    assert "p.PDF33" in answer


def test_multi_query_fusion_rewards_passages_found_by_several_formulations() -> None:
    shared = {
        "chunk_id": 220,
        "pdf_page": 33,
        "text": "Tableau des indicateurs bancaires",
        "score": 0.4,
        "lexical_score": 0.3,
        "keyword_overlap": 3,
        "query_keyword_count": 6,
    }
    other = {
        "chunk_id": 690,
        "pdf_page": 113,
        "text": "Autre notion de dépôt",
        "score": 0.5,
        "lexical_score": 0.2,
        "keyword_overlap": 2,
        "query_keyword_count": 6,
    }
    fused = RAGIndex.fuse_ranked_results(
        [[other, shared], [shared]], max_results=5
    )
    assert fused[0]["chunk_id"] == 220
    assert fused[0]["query_hits"] == 2
