"""Vérifie la journalisation consentie et la limite de questions par session."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import api.app as appmod
from api.app import create_app
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
    assert lignes[0][0] == "s1"
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
