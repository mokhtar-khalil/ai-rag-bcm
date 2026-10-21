"""Non-régressions des questions de démonstration ayant échoué au rejeu réel.

Aucun appel réseau : planificateur, reranker, générateur et OCR sont simulés.
L'index utilisé est celui du dépôt, en recherche lexicale seule.
"""

from __future__ import annotations

from typing import Any

from api.app import _definition_term, _elliptical_followup, create_app
from core.config import get_settings
from tests.conftest import generation_diffusee


AUDITOR_HISTORY = [
    {
        "role": "user",
        "content": "Y a-t-il un rapport de l'auditeur externe dans le rapport ?",
    },
    {
        "role": "assistant",
        "content": (
            "Oui. Le Rapport annuel 2025 comporte une section intitulée "
            "« Rapport de l’Auditeur Externe ». [p. PDF 119]"
        ),
    },
]


def _settings(provider: str = "extractive", chart_analysis: bool = False) -> Any:
    return get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": provider,
            "SEMANTIC_RETRIEVAL": "false",
            "CHART_ANALYSIS_ENABLED": "true" if chart_analysis else "false",
            "OPEN_BROWSER": "false",
        }
    )


def _no_planning(monkeypatch) -> None:
    monkeypatch.setattr(
        "api.app.plan_queries_openai",
        lambda question, settings: {
            "queries": [question],
            "ambiguous": False,
            "suggestions": [],
        },
    )


def test_definition_term_targets_only_acronyms() -> None:
    assert _definition_term("Que signifie SENAD ?") == "SENAD"
    assert _definition_term("Que veut dire BBCM dans le rapport ?") == "BBCM"
    assert _definition_term("C'est quoi le résumé de cette section ?") == ""
    assert _definition_term("Quel est l'encours SENAD ?") == ""


def test_undeveloped_acronym_is_described_instead_of_refused(monkeypatch) -> None:
    """SENAD n'est développé nulle part : le reranker rejetait tout passage."""
    captured: dict[str, Any] = {}
    _no_planning(monkeypatch)
    monkeypatch.setattr("api.app.rerank_openai", lambda question, results, settings: [])

    def fake_answer(provider, question, results, history, settings, language=None):
        captured["results"] = results
        return (
            "Le développé de « SENAD » n’est pas donné ; les titres SENAD sont des "
            "instruments d’open market [p. PDF 26]."
        )

    monkeypatch.setattr("api.app.stream_answer", generation_diffusee(fake_answer))
    response = create_app(settings_override=_settings("openai")).test_client().post(
        "/api/ask", json={"question": "Que signifie SENAD ?", "language": "fr"}
    )
    assert response.status_code == 200
    assert response.json["grounded"] is True
    assert captured["results"], "le générateur doit recevoir les passages du sigle"
    assert all("senad" in item["text"].casefold() for item in captured["results"][:3])
    assert {25, 26} & {int(item["pdf_page"]) for item in captured["results"]}


def test_year_only_followup_is_rewritten_as_a_standalone_question() -> None:
    history = [
        {
            "role": "user",
            "content": "Quel a été le taux d'inflation moyen en Mauritanie en 2025 ?",
        },
        {"role": "assistant", "content": "1,6 % en 2025 [p. PDF 23]."},
    ]
    assert _elliptical_followup("Et en 2024 ?", history) == (
        "Quel a été le taux d'inflation moyen en Mauritanie en 2024 ?"
    )
    # Une question autonome, longue ou sans année n'est jamais réécrite.
    assert _elliptical_followup("Et les exportations ?", history) == "Et les exportations ?"
    assert _elliptical_followup("Quel était le PIB en 2024 ?", history) == (
        "Quel était le PIB en 2024 ?"
    )
    assert _elliptical_followup("Et en 2024 ?", []) == "Et en 2024 ?"


def test_inflation_followup_reaches_the_2024_table_row(monkeypatch) -> None:
    """La ligne « INDICE GLOBAL … 2,5% 1,6% » (p. PDF 23) porte le taux 2024."""
    captured: dict[str, Any] = {}

    def fake_answer(provider, question, results, history, settings, language=None):
        captured["question"] = question
        captured["results"] = results
        return "Le taux d’inflation moyen a été de 2,5 % en 2024 [p. PDF 23]."

    monkeypatch.setattr("api.app.stream_answer", generation_diffusee(fake_answer))
    # Les tests n'ont que la recherche lexicale : la question reprend donc le
    # libellé du rapport, que la recherche sémantique rapproche en production
    # de « taux d'inflation moyen ».
    response = create_app(settings_override=_settings()).test_client().post(
        "/api/ask",
        json={
            "question": "Et en 2024 ?",
            "language": "fr",
            "history": [
                {
                    "role": "user",
                    "content": (
                        "Quelle a été la progression de l'indice national des prix "
                        "à la consommation en moyenne annuelle en 2025 ?"
                    ),
                },
                {
                    "role": "assistant",
                    "content": "Le taux d’inflation moyen a été de 1,6 % en 2025 [p. PDF 23].",
                },
            ],
        },
    )
    assert response.status_code == 200
    assert response.json["memory_used"] is True
    assert "prix" in captured["question"] and "2024" in captured["question"]
    assert "2025" not in captured["question"]
    table_rows = [
        item
        for item in captured["results"]
        if int(item["pdf_page"]) == 23 and "INDICE GLOBAL" in item["text"]
    ]
    assert table_rows, "la ligne de l'indice global doit atteindre le générateur"
    assert "2023 2024 2025" in table_rows[0]["text"]
    assert response.json["sources"][0]["pdf_page"] == 23


def test_year_table_passages_restore_the_column_header() -> None:
    engine = create_app(settings_override=_settings()).extensions["bcm_engine"]
    engine.load()
    passages = engine.year_table_passages([23], {"2024"})
    assert passages and all(int(item["pdf_page"]) == 23 for item in passages)
    global_rows = [item for item in passages if "INDICE GLOBAL" in item["text"]]
    assert global_rows
    assert global_rows[0]["text"].startswith(
        "En-tête du tableau : Libellé Pond. 2023 2024 2025"
    )
    # Une année que l'en-tête ne couvre pas ne rattache aucun tableau.
    assert engine.year_table_passages([23], {"2019"}) == []


def test_chart_existence_without_chart_analysis_uses_the_report_text(monkeypatch) -> None:
    """Sans OCR (production), l'absence d'un graphique ne peut pas être affirmée."""
    captured: dict[str, Any] = {}

    def fake_answer(provider, question, results, history, settings, language=None):
        captured["results"] = results
        return (
            "Oui. Les graphes suivants présentent l’évolution trimestrielle des "
            "achats et ventes de devises [p. PDF 67]."
        )

    monkeypatch.setattr("api.app.stream_answer", generation_diffusee(fake_answer))
    response = create_app(settings_override=_settings()).test_client().post(
        "/api/ask",
        json={
            "question": "Y a-t-il un graphique sur les achats et ventes de devises ?",
            "language": "fr",
        },
    )
    assert response.status_code == 200
    assert not response.json["answer"].startswith("Non")
    assert response.json["grounded"] is True
    assert 67 in {int(item["pdf_page"]) for item in captured["results"]}
    # La page citée par la réponse remonte en tête des sources affichées.
    assert response.json["sources"][0]["pdf_page"] == 67


def test_scanned_section_followup_without_ocr_answers_honestly(monkeypatch) -> None:
    monkeypatch.setattr(
        "api.app.stream_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Une page illisible ne doit pas être confiée au générateur.")
        ),
    )
    response = create_app(settings_override=_settings()).test_client().post(
        "/api/ask",
        json={
            "question": "C'est quoi le résumé de cette section ?",
            "language": "fr",
            "history": AUDITOR_HISTORY,
        },
    )
    assert response.status_code == 200
    answer = response.json["answer"]
    assert "Rapport de l’Auditeur Externe" in answer
    assert "image numérisée" in answer
    assert "[p. PDF 119]" in answer
    assert "Je ne trouve pas" not in answer
    assert [source["pdf_page"] for source in response.json["sources"]] == [119]


def test_scanned_section_followup_survives_a_reranker_rejection(monkeypatch) -> None:
    """Le reranker écartait le titre seul de la page 119 : refus avant génération."""
    captured: dict[str, Any] = {}
    _no_planning(monkeypatch)
    monkeypatch.setattr("api.app.rerank_openai", lambda question, results, settings: [])
    monkeypatch.setattr(
        "api.app.render_chart_pages",
        lambda *args, **kwargs: [{"pdf_page": 119, "path": "/tmp/page-119.png"}],
    )
    monkeypatch.setattr(
        "api.app.extract_document_page_contexts",
        lambda *args, **kwargs: [
            {
                "chunk_id": -20119,
                "pdf_page": 119,
                "text": (
                    "[OCR documentaire local de la page PDF 119]\n"
                    "L'auditeur exprime une opinion sans réserve sur les états financiers."
                ),
                "score": 1.0,
                "lexical_score": 1.0,
                "semantic_score": None,
                "retrieval_mode": "document_ocr_local",
                "kind": "document_ocr",
            }
        ],
    )

    def fake_answer(provider, question, results, history, settings, language=None):
        captured["results"] = results
        return "L’auditeur exprime une opinion sans réserve [p. PDF 119]."

    monkeypatch.setattr("api.app.stream_answer", generation_diffusee(fake_answer))
    response = create_app(
        settings_override=_settings("openai", chart_analysis=True)
    ).test_client().post(
        "/api/ask",
        json={
            "question": "C'est quoi le résumé de cette section ?",
            "language": "fr",
            "history": AUDITOR_HISTORY,
        },
    )
    assert response.status_code == 200
    assert response.json["answer"] == (
        "L’auditeur exprime une opinion sans réserve [p. PDF 119]."
    )
    assert response.json["sources"][0]["pdf_page"] == 119
    assert captured["results"][0]["retrieval_mode"] == "document_ocr_local"
