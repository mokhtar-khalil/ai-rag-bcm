"""Vérifie le masquage des données personnelles, la purge et store=False."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.analytics as analytics
from api.analytics import (
    ANALYTICS_TABLES,
    ensure_schema,
    log_feedback,
    log_interaction,
    mask_personal_data,
    purge_expired,
    purge_expired_if_due,
)
from api.providers import generate_openai, plan_queries_openai, rerank_openai
from core.config import ConfigurationError, get_settings


def settings_de_test(tmp_path: Path, **overrides):
    """Réglages déterministes, avec une base d'analyse isolée par test."""
    base = {
        "APP_ENV": "test",
        "GENERATION_PROVIDER": "extractive",
        "OPEN_BROWSER": "false",
        "INDEX_PATH": str(tmp_path / "index.joblib"),
    }
    return get_settings({**base, **overrides})


def _db(settings) -> sqlite3.Connection:
    return sqlite3.connect(settings.index_path.parent / "analytics.db")


# --- Masquage ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Écrivez-moi à sidi.ahmed@gmail.com svp", "Écrivez-moi à [EMAIL] svp"),
        ("contact: a_b+c@bcm.gov.mr.", "contact: [EMAIL]."),
        ("Mon numéro : 22 12 34 56", "Mon numéro : [TEL]"),
        ("Mon numéro : 36123456", "Mon numéro : [TEL]"),
        ("appelez le 45-25-22-06", "appelez le [TEL]"),
        ("Tel +222 46 12 34 56 merci", "Tel [TEL] merci"),
        ("Tel +22246123456", "Tel [TEL]"),
        ("en France +33 6 12 34 56 78", "en France [TEL]"),
        ("ou 0033612345678", "ou [TEL]"),
        ("Mon NNI est 1234567890.", "Mon NNI est [ID]."),
        ("رقمي الوطني 9876543210 من فضلكم", "رقمي الوطني [ID] من فضلكم"),
        ("بريدي test@example.org", "بريدي [EMAIL]"),
    ],
)
def test_personal_data_is_masked(text: str, expected: str) -> None:
    assert mask_personal_data(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "L'inflation a atteint 4,0 % en 2025.",
        "Le taux directeur est passé de 8 % à 7,5 %.",
        "Voir le rapport annuel, p. 64 et p. PDF 120.",
        "Un plafond de 300 000 MRU par opération.",
        "Le total du bilan atteint 1 234 567 890 MRU.",
        "Les dépôts s'élèvent à 45000000 MRU.",
        "Une population de 123456789 habitants.",
        "Évolution sur la période 2020-2025 et 2024/2025.",
        "Croissance de 4,6 % du PIB réel, contre 6,5 % en 2023.",
        "Réserves : 2 345,67 millions USD, soit 5,8 mois d'importations.",
        "Le ratio passe de 12,34 à 22,45.",
        "Montant de 1 500 000 000 UM (ancienne ouguiya).",
        "Norme ISO 20022 et circulaire n° 004/GR/2024.",
        "",
    ],
)
def test_economic_figures_are_left_intact(text: str) -> None:
    assert mask_personal_data(text) == text


def test_interaction_and_feedback_are_masked_before_insertion(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path, ANALYTICS_RETENTION_DAYS="0")
    ensure_schema(settings)
    log_interaction(
        settings,
        response_id="r1",
        request_id="q1",
        session_id="s1",
        language="fr",
        question="Je suis joignable au 22 12 34 56, NNI 1234567890. Taux 4,0 % en 2025 ?",
        answer="Écrivez à info@bcm.mr ; le taux est de 4,0 % (p. 64).",
        status="answered",
        grounded=True,
        clarification_needed=False,
        memory_used=False,
        chart_analysis=False,
        sources=[],
        latency_ms=12.0,
        model_calls=[],
    )
    log_feedback(
        settings,
        "s1",
        "fr",
        "Mon courriel : moi@exemple.fr",
        "Réponse sur 300 000 MRU.",
        "down",
        response_id="r1",
        comment="Rappelez-moi au +222 46 12 34 56",
    )
    with _db(settings) as conn:
        question, answer = conn.execute(
            "SELECT question, answer FROM logged_questions"
        ).fetchone()
        fb_question, fb_answer, comment = conn.execute(
            "SELECT question, answer, comment FROM answer_feedback"
        ).fetchone()
    assert question == "Je suis joignable au [TEL], NNI [ID]. Taux 4,0 % en 2025 ?"
    assert answer == "Écrivez à [EMAIL] ; le taux est de 4,0 % (p. 64)."
    assert fb_question == "Mon courriel : [EMAIL]"
    assert fb_answer == "Réponse sur 300 000 MRU."
    assert comment == "Rappelez-moi au [TEL]"


# --- Purge ------------------------------------------------------------------


def _insert_rows(settings, created_at: str) -> None:
    with _db(settings) as conn:
        conn.execute(
            "INSERT INTO logged_questions (session_id, language, question, answer, created_at) "
            "VALUES ('s', 'fr', 'q', 'a', ?)",
            (created_at,),
        )
        conn.execute(
            "INSERT INTO answer_feedback (session_id, language, question, answer, rating, created_at) "
            "VALUES ('s', 'fr', 'q', 'a', 'up', ?)",
            (created_at,),
        )
        conn.execute(
            "INSERT INTO model_calls (response_id, operation, provider, model, success, created_at) "
            "VALUES ('r', 'generation', 'openai', 'm', 1, ?)",
            (created_at,),
        )
        conn.execute(
            "INSERT INTO ui_events (event_type, session_id, language, created_at) "
            "VALUES ('widget_opened', 's', 'fr', ?)",
            (created_at,),
        )


def _counts(settings) -> dict[str, int]:
    with _db(settings) as conn:
        return {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ANALYTICS_TABLES
        }


def test_retention_defaults_to_90_days_and_is_validated() -> None:
    assert get_settings({"APP_ENV": "test"}).analytics_retention_days == 90
    assert (
        get_settings({"APP_ENV": "test", "ANALYTICS_RETENTION_DAYS": "0"})
        .analytics_retention_days
        == 0
    )
    for invalid in ("-1", "abc", "4000"):
        with pytest.raises(ConfigurationError):
            get_settings({"APP_ENV": "test", "ANALYTICS_RETENTION_DAYS": invalid})


def test_purge_deletes_only_expired_rows_in_all_four_tables(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path, ANALYTICS_RETENTION_DAYS="90")
    ensure_schema(settings)
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    _insert_rows(settings, "2026-07-01T08:00:00.000Z")  # 109 jours : expirée
    _insert_rows(settings, "2026-07-20T11:59:59.999Z")  # juste avant la limite
    _insert_rows(settings, "2026-07-20T12:00:00.001Z")  # juste après la limite
    _insert_rows(settings, "2026-10-17T09:00:00.000Z")  # récente

    deleted = purge_expired(settings, now)

    assert deleted == {table: 2 for table in ANALYTICS_TABLES}
    assert _counts(settings) == {table: 2 for table in ANALYTICS_TABLES}


def test_purge_keeps_rows_created_with_the_default_timestamp(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path, ANALYTICS_RETENTION_DAYS="1")
    ensure_schema(settings)
    with _db(settings) as conn:
        conn.execute(
            "INSERT INTO ui_events (event_type, session_id, language) "
            "VALUES ('widget_opened', 's', 'fr')"
        )
    assert purge_expired(settings)["ui_events"] == 0
    assert _counts(settings)["ui_events"] == 1


def test_purge_is_disabled_when_retention_is_zero(tmp_path: Path) -> None:
    settings = settings_de_test(tmp_path, ANALYTICS_RETENTION_DAYS="0")
    ensure_schema(settings)
    _insert_rows(settings, "2000-01-01T00:00:00.000Z")
    assert purge_expired(settings) == {}
    assert _counts(settings) == {table: 1 for table in ANALYTICS_TABLES}


def test_purge_if_due_is_throttled(tmp_path: Path, monkeypatch) -> None:
    settings = settings_de_test(tmp_path, ANALYTICS_RETENTION_DAYS="30")
    ensure_schema(settings)
    monkeypatch.setattr(analytics, "_last_purge_at", None)
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)

    _insert_rows(settings, "2026-01-01T00:00:00.000Z")
    purge_expired_if_due(settings, now)
    assert _counts(settings)["logged_questions"] == 0

    _insert_rows(settings, "2026-01-01T00:00:00.000Z")
    purge_expired_if_due(settings, now + timedelta(hours=1))
    assert _counts(settings)["logged_questions"] == 1

    purge_expired_if_due(settings, now + timedelta(hours=7))
    assert _counts(settings)["logged_questions"] == 0


def test_purge_failure_never_raises(tmp_path: Path, monkeypatch) -> None:
    settings = settings_de_test(tmp_path)
    monkeypatch.setattr(analytics, "_last_purge_at", None)

    def broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(analytics, "purge_expired", broken)
    purge_expired_if_due(settings)


def test_purge_uses_a_typed_parameter_for_postgres(monkeypatch) -> None:
    settings = get_settings(
        {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql://user@localhost/db",
            "ANALYTICS_RETENTION_DAYS": "90",
        }
    )
    executed: list[tuple[str, tuple]] = []

    class FakeConnection:
        def execute(self, sql, params=()):
            executed.append((sql, params))
            return SimpleNamespace(rowcount=3)

        def commit(self):
            pass

    class FakeContext:
        def __enter__(self):
            return FakeConnection()

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(analytics, "_connection", lambda _settings: FakeContext())
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)

    deleted = purge_expired(settings, now)

    assert deleted == {table: 3 for table in ANALYTICS_TABLES}
    assert [sql for sql, _ in executed] == [
        f"DELETE FROM {table} WHERE created_at < %s" for table in ANALYTICS_TABLES
    ]
    assert all(params == (now - timedelta(days=90),) for _, params in executed)


# --- OpenAI store=False -----------------------------------------------------


def test_every_openai_call_disables_server_side_storage(monkeypatch) -> None:
    calls: list[dict] = []
    outputs = iter(
        [
            '{"queries": [], "ambiguous": false, "suggestions": []}',
            '{"chunk_ids": [1]}',
            "Le taux est de 4,0 % [p. PDF 64].",
        ]
    )

    def create_response(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text=next(outputs))

    fake_client = SimpleNamespace(responses=SimpleNamespace(create=create_response))
    monkeypatch.setitem(
        sys.modules, "openai", SimpleNamespace(OpenAI=lambda: fake_client)
    )
    settings = get_settings(
        {"APP_ENV": "test", "GENERATION_PROVIDER": "openai", "OPEN_BROWSER": "false"}
    )
    results = [{"chunk_id": 1, "pdf_page": 64, "text": "Taux : 4,0 %.", "score": 0.5}]

    plan_queries_openai("Quel est le taux ?", settings)
    rerank_openai("Quel est le taux ?", results, settings)
    generate_openai("Quel est le taux ?", results, [], settings)

    assert len(calls) == 3
    assert all(call.get("store") is False for call in calls)
