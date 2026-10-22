"""Clarification déterministe des termes polysémiques du corpus BCM."""

import json
from pathlib import Path

import pytest

from api.ambiguity import lexical_ambiguity
from api.app import create_app
from core.config import get_settings


@pytest.mark.parametrize(
    "question",
    [
        "Les dépôts des banques ont-ils augmenté ?",
        "Quel est le montant des dépôts dans les banques ?",
        "Combien d'argent les banques ont-elles déposé ?",
        "Quel est le solde ?",
        "Quel est le niveau des réserves ?",
        "Quel est le taux ?",
        "Quel est le taux actuel de la BCM ?",
    ],
)
def test_polysemous_questions_are_detected(question: str) -> None:
    ambiguity = lexical_ambiguity(question)
    assert ambiguity is not None
    assert len(ambiguity["suggestions"]) >= 2


@pytest.mark.parametrize(
    "question",
    [
        "Comment ont évolué les dépôts de la clientèle des banques entre 2024 et 2025 ?",
        "Quelle part de la masse monétaire était constituée de dépôts à vue ?",
        "Quel a été le solde budgétaire en 2025 ?",
        "Quel était le niveau des réserves officielles brutes fin décembre 2025 ?",
        "Quel est le taux directeur de la BCM ?",
        "Quel est le taux d'intérêt ?",
        "Quels revenus les placements des réserves ont-ils générés au premier semestre 2026 ?",
    ],
)
def test_precise_questions_are_not_flagged(question: str) -> None:
    assert lexical_ambiguity(question) is None


def test_proposed_rephrasings_are_never_ambiguous_themselves() -> None:
    for question in (
        "Le montant de dépôt dans les banques en 2025",
        "Quel est le solde ?",
        "Quel est le niveau des réserves ?",
        "Quel est le taux ?",
    ):
        for suggestion in lexical_ambiguity(question)["suggestions"]:
            assert lexical_ambiguity(suggestion) is None, suggestion


def test_period_of_the_question_is_kept() -> None:
    suggestions = lexical_ambiguity(
        "Le montant de dépôt dans les banques : comparaison entre 2024 et 2025"
    )["suggestions"]
    assert all("entre 2024 et 2025" in value for value in suggestions)
    assert "en 2024" in lexical_ambiguity("Quel était le solde en 2024 ?")["suggestions"][0]


def test_evaluation_sets_trigger_only_the_deposit_case() -> None:
    # Le banc d'évaluation mesure les clarifications parasites : seule la
    # question volontairement ambiguë sur les dépôts doit déclencher la règle.
    triggered = []
    for name in ("questions.jsonl", "questions_lettres_2026.jsonl", "questions_arabe.jsonl"):
        path = Path(__file__).resolve().parents[1] / "evaluation" / name
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            if lexical_ambiguity(case["question"]):
                triggered.append(case["id"])
    assert triggered == ["bank_customer_deposits_01"]


def _client():
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    return create_app(settings_override=settings).test_client()


def test_api_explains_the_ambiguity_and_offers_every_meaning() -> None:
    response = _client().post("/api/ask", json={"question": "Quel est le solde ?"})
    assert response.status_code == 200
    body = response.json
    assert body["clarification_needed"] is True
    assert body["grounded"] is False
    assert len(body["suggestions"]) == 3
    assert body["sources"] == []
    assert body["answer"].startswith("« Solde » peut désigner plusieurs soldes")
    assert all(value in body["answer"] for value in body["suggestions"])


def test_api_deposits_question_offers_both_deposit_meanings() -> None:
    response = _client().post(
        "/api/ask", json={"question": "Les dépôts des banques ont-ils augmenté ?"}
    )
    body = response.json
    assert body["clarification_needed"] is True
    assert "Banque Centrale" in body["answer"]
    assert any("clientèle" in value for value in body["suggestions"])
    assert any("à la Banque Centrale" in value for value in body["suggestions"])


def test_choosing_a_suggestion_is_answered_directly() -> None:
    client = _client()
    first = client.post("/api/ask", json={"question": "Quel est le taux ?"}).json
    choice = first["suggestions"][0]
    response = client.post(
        "/api/ask",
        json={
            "question": choice,
            "history": [
                {"role": "user", "content": "Quel est le taux ?"},
                {"role": "assistant", "content": first["answer"]},
            ],
        },
    )
    assert response.json["clarification_needed"] is False


def test_arabic_questions_keep_the_planner_path() -> None:
    response = _client().post(
        "/api/ask", json={"question": "ما هو سعر الفائدة الرئيسي للبنك المركزي الموريتاني؟"}
    )
    assert response.json["clarification_needed"] is False
