"""Vérifie la journalisation consentie et la limite de questions par session."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.app as appmod
from api.analytics import analytics_snapshot
from api.app import create_app
from api.usage import (
    begin_usage_collection,
    model_call_started,
    record_model_response,
    reset_usage_collection,
    usage_snapshot,
)
from core.config import get_settings


def settings_de_test(tmp_path: Path, **overrides) -> "get_settings":  # type: ignore[valid-type]
    """Réglages déterministes, avec une base d'analyse isolée par test."""
    base = {
        "APP_ENV": "test",
        "GENERATION_PROVIDER": "extractive",
        "OPEN_BROWSER": "false",
        "INDEX_PATH": str(tmp_path / "index.joblib"),
    }
    return get_settings({**base, **overrides})


def _lignes(settings) -> list[tuple]:
    """Lit le contenu de la base SQLite locale utilisée par les tests."""
    db = settings.index_path.parent / "analytics.db"
    if not db.exists():
        return []
    return sqlite3.connect(db).execute(
        "SELECT session_id, language, question, answer FROM logged_questions"
    ).fetchall()


def test_question_is_logged_only_with_consent(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path)
    client = create_app(settings_override=settings).test_client()

    client.post(
        "/api/ask",
        json={
            "question": "Quel est le taux directeur ?",
            "session_id": "s1",
            "consent_analytics": False,
        },
    )
    client.post(
        "/api/ask",
        json={
            "question": "Quel est le taux de croissance du PIB réel en 2025 ?",
            "session_id": "s1",
            "consent_analytics": True,
        },
    )

    lignes = _lignes(settings)
    assert len(lignes) == 1
    assert lignes[0][0] != "s1"
    assert len(lignes[0][0]) == 64
    assert "croissance" in lignes[0][2]


def test_question_without_session_id_is_never_logged(tmp_path: Path) -> None:
    """Un ancien client, sans session_id, ne doit rien écrire même consentant."""
    settings = settings_de_test(tmp_path)
    client = create_app(settings_override=settings).test_client()
    client.post(
        "/api/ask",
        json={"question": "Question sans session", "consent_analytics": True},
    )
    assert _lignes(settings) == []


def test_session_limit_blocks_after_the_configured_count(tmp_path: Path) -> None:
    settings = settings_de_test(
        tmp_path, SESSION_MAX_QUESTIONS="3", SESSION_IDLE_MINUTES="30"
    )
    client = create_app(settings_override=settings).test_client()

    statuts = [
        client.post(
            "/api/ask", json={"question": f"Question {i}", "session_id": "limite"}
        ).status_code
        for i in range(4)
    ]
    assert statuts == [200, 200, 200, 429]


def test_session_limit_response_carries_a_distinct_reason(tmp_path: Path) -> None:
    """Le widget doit pouvoir distinguer ce plafond de la limitation de débit générique."""
    settings = settings_de_test(
        tmp_path, SESSION_MAX_QUESTIONS="1", SESSION_IDLE_MINUTES="30"
    )
    client = create_app(settings_override=settings).test_client()
    client.post("/api/ask", json={"question": "Première", "session_id": "s"})
    response = client.post("/api/ask", json={"question": "Seconde", "session_id": "s"})
    assert response.status_code == 429
    assert response.json["reason"] == "session_limit"


def test_session_limit_is_isolated_per_session(tmp_path: Path) -> None:
    settings = settings_de_test(
        tmp_path, SESSION_MAX_QUESTIONS="1", SESSION_IDLE_MINUTES="30"
    )
    client = create_app(settings_override=settings).test_client()
    client.post("/api/ask", json={"question": "Une question", "session_id": "a"})
    response = client.post("/api/ask", json={"question": "Une autre", "session_id": "b"})
    assert response.status_code == 200


def test_session_limit_resets_after_the_idle_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_de_test(
        tmp_path, SESSION_MAX_QUESTIONS="2", SESSION_IDLE_MINUTES="1"
    )
    horloge = {"t": 0.0}
    monkeypatch.setattr(appmod, "perf_counter", lambda: horloge["t"])
    client = create_app(settings_override=settings).test_client()

    for _ in range(2):
        assert client.post(
            "/api/ask", json={"question": "q", "session_id": "idle"}
        ).status_code == 200
    assert client.post(
        "/api/ask", json={"question": "q", "session_id": "idle"}
    ).status_code == 429

    horloge["t"] += 61  # plus d'une minute d'inactivité
    assert client.post(
        "/api/ask", json={"question": "q", "session_id": "idle"}
    ).status_code == 200


def test_feedback_is_logged_regardless_of_analytics_consent(tmp_path: Path) -> None:
    """Le pouce haut/bas est une action explicite, indépendante du consentement continu."""
    settings = settings_de_test(tmp_path)
    client = create_app(settings_override=settings).test_client()
    answer = client.post(
        "/api/ask",
        json={
            "question": "Quel est le taux directeur ?",
            "session_id": "s1",
            "consent_analytics": False,
        },
    )
    assert answer.status_code == 200
    response = client.post(
        "/api/feedback",
        json={
            "session_id": "s1",
            "language": "fr",
            "question": "Quel est le taux directeur ?",
            "answer": "6,75 %.",
            "rating": "up",
            "reason": "helpful",
            "resolved": True,
            "response_id": answer.json["response_id"],
            "feedback_token": answer.json["feedback_token"],
        },
    )
    assert response.status_code == 200
    db = settings.index_path.parent / "analytics.db"
    rows = sqlite3.connect(db).execute(
        "SELECT session_id, rating, question, answer, reason, resolved "
        "FROM answer_feedback"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] != "s1"
    assert rows[0][1:] == (
        "up", "Quel est le taux directeur ?", "6,75 %.", "helpful", 1
    )


def test_feedback_rejects_an_invalid_rating(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path)
    client = create_app(settings_override=settings).test_client()
    response = client.post(
        "/api/feedback", json={"question": "q", "answer": "a", "rating": "neutre"}
    )
    assert response.status_code == 400


def test_feedback_requires_question_and_answer(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path)
    client = create_app(settings_override=settings).test_client()
    response = client.post("/api/feedback", json={"rating": "up"})
    assert response.status_code == 400


def test_feedback_is_idempotent_for_one_response(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path)
    client = create_app(settings_override=settings).test_client()
    answer = client.post(
        "/api/ask",
        json={"question": "Question", "session_id": "s1"},
    ).json
    payload = {
        "session_id": "s1",
        "language": "fr",
        "question": "Question",
        "answer": "Réponse",
        "rating": "down",
        "reason": "incomplete",
        "resolved": False,
        "response_id": answer["response_id"],
        "feedback_token": answer["feedback_token"],
    }
    assert client.post("/api/feedback", json=payload).json["recorded"] is True
    assert client.post("/api/feedback", json=payload).json["recorded"] is False


def test_feedback_rejects_a_forged_token(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path)
    client = create_app(settings_override=settings).test_client()
    response = client.post(
        "/api/feedback",
        json={
            "session_id": "s1",
            "question": "Question",
            "answer": "Réponse",
            "rating": "up",
            "response_id": "a" * 32,
            "feedback_token": "forged",
        },
    )
    assert response.status_code == 403


def test_ui_event_requires_consent_and_is_pseudonymized(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path)
    client = create_app(settings_override=settings).test_client()
    ignored = client.post(
        "/api/events",
        json={"event_type": "widget_opened", "session_id": "s1"},
    )
    assert ignored.json["status"] == "ignored"
    accepted = client.post(
        "/api/events",
        json={
            "event_type": "widget_opened",
            "session_id": "s1",
            "language": "fr",
            "consent_analytics": True,
        },
    )
    assert accepted.status_code == 200
    db = settings.index_path.parent / "analytics.db"
    rows = sqlite3.connect(db).execute(
        "SELECT event_type, session_id FROM ui_events"
    ).fetchall()
    assert rows[0][0] == "widget_opened"
    assert rows[0][1] != "s1"


def test_interaction_has_response_metadata(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path)
    client = create_app(settings_override=settings).test_client()
    response = client.post(
        "/api/ask",
        json={
            "question": "Quel est le taux de croissance du PIB réel en 2025 ?",
            "session_id": "s1",
            "consent_analytics": True,
        },
    )
    assert response.status_code == 200
    assert len(response.json["response_id"]) == 32
    db = settings.index_path.parent / "analytics.db"
    row = sqlite3.connect(db).execute(
        "SELECT response_id, topic, status, latency_ms, source_count "
        "FROM logged_questions"
    ).fetchone()
    assert row[0] == response.json["response_id"]
    assert row[1] == "conjoncture_inflation"
    assert row[2] in {"answered", "refused", "clarification"}
    assert row[3] >= 0
    assert row[4] >= 0


def test_openai_usage_is_normalized() -> None:
    token = begin_usage_collection()
    try:
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=40,
                total_tokens=140,
                input_tokens_details=SimpleNamespace(cached_tokens=25),
                output_tokens_details=SimpleNamespace(reasoning_tokens=10),
            )
        )
        record_model_response(
            "generation", "openai", "modele-test", response, model_call_started()
        )
        call = usage_snapshot()[0]
    finally:
        reset_usage_collection(token)
    assert call["input_tokens"] == 100
    assert call["cached_input_tokens"] == 25
    assert call["output_tokens"] == 40
    assert call["reasoning_tokens"] == 10
    assert call["total_tokens"] == 140


def test_analytics_snapshot_contains_aggregated_kpis(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path)
    client = create_app(settings_override=settings).test_client()
    client.post(
        "/api/ask",
        json={
            "question": "Quel est le taux de croissance du PIB réel en 2025 ?",
            "session_id": "s1",
            "consent_analytics": True,
        },
    )
    snapshot = analytics_snapshot(settings, days=1)
    assert snapshot["overview"]["interactions"] == 1
    assert snapshot["overview"]["consented_sessions"] == 1
    assert snapshot["by_language"] == {"fr": 1}


def test_admin_analytics_endpoint_is_protected(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path, ANALYTICS_ADMIN_TOKEN="dashboard-secret")
    client = create_app(settings_override=settings).test_client()
    assert client.get("/api/admin/analytics").status_code == 401
    response = client.get(
        "/api/admin/analytics?days=7",
        headers={"Authorization": "Bearer dashboard-secret"},
    )
    assert response.status_code == 200
    assert response.json["period_days"] == 7
