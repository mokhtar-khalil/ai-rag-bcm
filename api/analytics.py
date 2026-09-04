"""Journalisation des questions/réponses, soumise au consentement de l'utilisateur.

Deux moteurs sont pris en charge selon la configuration :

- Postgres, via ``DATABASE_URL`` (fourni par exemple par l'extension Railway) :
  seul choix durable en production, le disque d'un conteneur Railway ne
  survivant pas à un redéploiement sans volume attaché.
- SQLite (fichier local sous ``storage/``) : sans dépendance externe, pratique
  en développement, mais non durable sur Railway dans les mêmes conditions.

Chaque insertion est best-effort : une panne de la base ne doit jamais faire
échouer une réponse déjà produite pour l'utilisateur. Le module ne stocke que
la question, la réponse, la langue et l'horodatage — jamais l'adresse IP ni un
identifiant permettant de retrouver la personne, conformément au périmètre
annoncé dans le popup de consentement du widget.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from core.config import Settings


CREATE_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS logged_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    language TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""

CREATE_TABLE_POSTGRES = """
CREATE TABLE IF NOT EXISTS logged_questions (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    language TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# Le pouce haut/bas est une action explicite et ponctuelle de l'utilisateur,
# distincte du consentement de journalisation continue : elle est collectée
# qu'il ait consenti ou non à la journalisation des questions, puisque le
# clic lui-même en tient lieu pour cette seule réponse.
CREATE_FEEDBACK_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS answer_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    language TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    rating TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""

CREATE_FEEDBACK_TABLE_POSTGRES = """
CREATE TABLE IF NOT EXISTS answer_feedback (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    language TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    rating TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# Les tables historiques restent en place. ``ensure_schema`` leur ajoute les
# colonnes ci-dessous sans supprimer les lignes déjà collectées.
ANALYTICS_COLUMNS_SQLITE = {
    "logged_questions": {
        "response_id": "TEXT", "request_id": "TEXT", "topic": "TEXT",
        "provider": "TEXT", "model": "TEXT", "status": "TEXT",
        "grounded": "INTEGER", "clarification_needed": "INTEGER",
        "memory_used": "INTEGER", "chart_analysis": "INTEGER",
        "source_count": "INTEGER", "sources_json": "TEXT",
        "retrieval_score_max": "REAL", "latency_ms": "REAL",
        "input_tokens": "INTEGER", "cached_input_tokens": "INTEGER",
        "output_tokens": "INTEGER", "reasoning_tokens": "INTEGER",
        "total_tokens": "INTEGER", "model_call_count": "INTEGER",
        "fallback_used": "INTEGER", "error_type": "TEXT",
    },
    "answer_feedback": {
        "response_id": "TEXT", "reason": "TEXT", "resolved": "INTEGER",
        "comment": "TEXT",
    },
}

ANALYTICS_COLUMNS_POSTGRES = {
    "logged_questions": {
        "response_id": "TEXT", "request_id": "TEXT", "topic": "TEXT",
        "provider": "TEXT", "model": "TEXT", "status": "TEXT",
        "grounded": "BOOLEAN", "clarification_needed": "BOOLEAN",
        "memory_used": "BOOLEAN", "chart_analysis": "BOOLEAN",
        "source_count": "INTEGER", "sources_json": "TEXT",
        "retrieval_score_max": "DOUBLE PRECISION", "latency_ms": "DOUBLE PRECISION",
        "input_tokens": "BIGINT", "cached_input_tokens": "BIGINT",
        "output_tokens": "BIGINT", "reasoning_tokens": "BIGINT",
        "total_tokens": "BIGINT", "model_call_count": "INTEGER",
        "fallback_used": "BOOLEAN", "error_type": "TEXT",
    },
    "answer_feedback": {
        "response_id": "TEXT", "reason": "TEXT", "resolved": "BOOLEAN",
        "comment": "TEXT",
    },
}

CREATE_MODEL_CALLS_SQLITE = """
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id TEXT NOT NULL,
    request_id TEXT,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    success INTEGER NOT NULL,
    error_type TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""

CREATE_MODEL_CALLS_POSTGRES = """
CREATE TABLE IF NOT EXISTS model_calls (
    id BIGSERIAL PRIMARY KEY,
    response_id TEXT NOT NULL,
    request_id TEXT,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    cached_input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    reasoning_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens BIGINT NOT NULL DEFAULT 0,
    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    success BOOLEAN NOT NULL,
    error_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_UI_EVENTS_SQLITE = """
CREATE TABLE IF NOT EXISTS ui_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    response_id TEXT,
    language TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""

CREATE_UI_EVENTS_POSTGRES = """
CREATE TABLE IF NOT EXISTS ui_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    response_id TEXT,
    language TEXT NOT NULL,
    metadata_json TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

ALLOWED_UI_EVENTS = {
    "consent_accepted", "widget_opened", "question_submitted",
    "response_received", "suggestion_clicked", "sources_opened",
    "source_link_clicked", "feedback_submitted", "session_limit_reached",
}

ALLOWED_FEEDBACK_REASONS = {
    "helpful", "incorrect", "incomplete", "missing_source", "unclear",
    "too_slow", "other", "",
}

TOPIC_KEYWORDS = {
    "systemes_paiement": ("paiement", "virement", "ach", "rtgs", "gimtel", "الدفع", "تحويل"),
    "secteur_bancaire": ("banque", "bancaire", "depot", "credit", "مصرف", "بنك", "ودائع", "قروض"),
    "politique_monetaire": ("politique monetaire", "taux directeur", "liquidite", "نقدية", "السيولة"),
    "change_devises": ("change", "devise", "euro", "dollar", "usd", "eur", "صرف", "عملات"),
    "statistiques_monetaires": ("agregat", "monnaie", "masse monetaire", "الكتلة النقدية"),
    "inclusion_financiere": ("inclusion", "education financiere", "شمول مالي"),
    "etats_financiers": ("situation financiere", "resultat net", "capitaux propres", "audit", "مالي", "التدقيق"),
    "organisation_bcm": ("organigramme", "gouverneur", "direction generale", "هيكل تنظيمي", "المحافظ"),
    "conjoncture_inflation": ("inflation", "pib", "croissance", "conjoncture", "تضخم", "نمو", "الناتج"),
}


def _is_postgres(database_url: str) -> bool:
    """Distingue une URL Postgres d'une absence de configuration."""
    return database_url.startswith(("postgres://", "postgresql://"))


@contextmanager
def _connection(settings: Settings) -> Iterator[Any]:
    """Ouvre une connexion vers le moteur configuré et la valide en sortie de bloc.

    ``psycopg`` (Postgres) et ``sqlite3`` exposent tous deux ``execute`` et
    ``commit`` sur l'objet connexion : le reste du module s'écrit donc sans
    distinguer les deux moteurs au-delà de ce point.
    """
    if _is_postgres(settings.analytics_database_url):
        import psycopg  # import local : dépendance absente en développement

        with psycopg.connect(settings.analytics_database_url) as conn:
            yield conn
    else:
        # Le fichier vit à côté de l'index, dans storage/ : non durable sur
        # Railway sans volume attaché, ce qui est documenté plutôt que masqué.
        path = settings.index_path.parent / "analytics.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=5) as conn:
            yield conn


def ensure_schema(settings: Settings) -> None:
    """Crée ou migre le schéma sans supprimer les données déjà collectées."""
    postgres = _is_postgres(settings.analytics_database_url)
    with _connection(settings) as conn:
        conn.execute(CREATE_TABLE_POSTGRES if postgres else CREATE_TABLE_SQLITE)
        conn.execute(
            CREATE_FEEDBACK_TABLE_POSTGRES if postgres else CREATE_FEEDBACK_TABLE_SQLITE
        )
        migrations = (
            ANALYTICS_COLUMNS_POSTGRES if postgres else ANALYTICS_COLUMNS_SQLITE
        )
        for table, columns in migrations.items():
            if postgres:
                for name, sql_type in columns.items():
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {sql_type}"
                    )
            else:
                existing = {
                    str(row[1])
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for name, sql_type in columns.items():
                    if name not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
        conn.execute(CREATE_MODEL_CALLS_POSTGRES if postgres else CREATE_MODEL_CALLS_SQLITE)
        conn.execute(CREATE_UI_EVENTS_POSTGRES if postgres else CREATE_UI_EVENTS_SQLITE)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_logged_response_id "
            "ON logged_questions(response_id) WHERE response_id IS NOT NULL"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_response_session "
            "ON answer_feedback(response_id, session_id) WHERE response_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_logged_created_at ON logged_questions(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_calls_created_at ON model_calls(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ui_events_created_at ON ui_events(created_at)"
        )
        conn.commit()


def pseudonymize_session(settings: Settings, session_id: str) -> str:
    """Transforme l'identifiant navigateur en pseudonyme stable non réversible."""
    secret = settings.analytics_hash_salt or "bcm-analytics-local"
    return hmac.new(
        secret.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _fold(value: str) -> str:
    """Normalise les accents et la casse pour la classification thématique."""
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def classify_topic(question: str) -> str:
    """Classe localement une question sans consommer de tokens de génération."""
    folded = _fold(question)
    scores = {
        topic: sum(_fold(keyword) in folded for keyword in keywords)
        for topic, keywords in TOPIC_KEYWORDS.items()
    }
    topic, score = max(scores.items(), key=lambda item: item[1])
    return topic if score else "autre"


def _aggregate_usage(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcule les totaux de tokens et identifie la génération principale."""
    generation = next(
        (call for call in reversed(calls) if call.get("operation") == "generation"),
        calls[-1] if calls else {},
    )
    return {
        "provider": str(generation.get("provider") or "local"),
        "model": str(generation.get("model") or "none"),
        "input_tokens": sum(int(call.get("input_tokens") or 0) for call in calls),
        "cached_input_tokens": sum(
            int(call.get("cached_input_tokens") or 0) for call in calls
        ),
        "output_tokens": sum(int(call.get("output_tokens") or 0) for call in calls),
        "reasoning_tokens": sum(
            int(call.get("reasoning_tokens") or 0) for call in calls
        ),
        "total_tokens": sum(int(call.get("total_tokens") or 0) for call in calls),
        "model_call_count": len(calls),
        "fallback_used": any(
            call.get("operation") == "generation" and not call.get("success", True)
            for call in calls
        ),
        "error_type": next(
            (str(call.get("error_type")) for call in reversed(calls) if call.get("error_type")),
            "",
        ),
    }


def log_interaction(
    settings: Settings,
    *,
    response_id: str,
    request_id: str,
    session_id: str,
    language: str,
    question: str,
    answer: str,
    status: str,
    grounded: bool,
    clarification_needed: bool,
    memory_used: bool,
    chart_analysis: bool,
    sources: list[dict[str, Any]],
    latency_ms: float,
    model_calls: list[dict[str, Any]],
) -> None:
    """Enregistre atomiquement l'interaction et les appels de modèles associés."""
    postgres = _is_postgres(settings.analytics_database_url)
    placeholder = "%s" if postgres else "?"
    usage = _aggregate_usage(model_calls)
    source_scores = [
        float(source.get("score") or 0)
        for source in sources
        if source.get("score") is not None
    ]
    columns = (
        "response_id", "request_id", "session_id", "language", "question",
        "answer", "topic", "provider", "model", "status", "grounded",
        "clarification_needed", "memory_used", "chart_analysis", "source_count",
        "sources_json", "retrieval_score_max", "latency_ms", "input_tokens",
        "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
        "model_call_count", "fallback_used", "error_type",
    )
    values = (
        response_id, request_id, pseudonymize_session(settings, session_id), language,
        question, answer, classify_topic(question), usage["provider"], usage["model"],
        status, grounded, clarification_needed, memory_used, chart_analysis,
        len(sources), json.dumps(sources, ensure_ascii=False, separators=(",", ":")),
        max(source_scores) if source_scores else None, round(latency_ms, 1),
        usage["input_tokens"], usage["cached_input_tokens"], usage["output_tokens"],
        usage["reasoning_tokens"], usage["total_tokens"], usage["model_call_count"],
        usage["fallback_used"], usage["error_type"],
    )
    with _connection(settings) as conn:
        conn.execute(
            f"INSERT INTO logged_questions ({', '.join(columns)}) VALUES "
            f"({', '.join([placeholder] * len(columns))})",
            values,
        )
        call_columns = (
            "response_id", "request_id", "operation", "provider", "model",
            "input_tokens", "cached_input_tokens", "output_tokens",
            "reasoning_tokens", "total_tokens", "latency_ms", "success", "error_type",
        )
        call_markers = ", ".join([placeholder] * len(call_columns))
        for call in model_calls:
            conn.execute(
                f"INSERT INTO model_calls ({', '.join(call_columns)}) "
                f"VALUES ({call_markers})",
                (
                    response_id, request_id, str(call.get("operation") or "unknown"),
                    str(call.get("provider") or "unknown"),
                    str(call.get("model") or "unknown"),
                    int(call.get("input_tokens") or 0),
                    int(call.get("cached_input_tokens") or 0),
                    int(call.get("output_tokens") or 0),
                    int(call.get("reasoning_tokens") or 0),
                    int(call.get("total_tokens") or 0),
                    float(call.get("latency_ms") or 0), bool(call.get("success", True)),
                    str(call.get("error_type") or ""),
                ),
            )
        conn.commit()


def log_question(
    settings: Settings, session_id: str, language: str, question: str, answer: str
) -> None:
    """Compatibilité avec les anciens appelants ; préférer ``log_interaction``."""
    response_id = hashlib.sha256(
        f"{session_id}:{question}:{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()
    log_interaction(
        settings,
        response_id=response_id,
        request_id="legacy",
        session_id=session_id,
        language=language,
        question=question,
        answer=answer,
        status="answered",
        grounded=False,
        clarification_needed=False,
        memory_used=False,
        chart_analysis=False,
        sources=[],
        latency_ms=0,
        model_calls=[],
    )


def log_feedback(
    settings: Settings,
    session_id: str,
    language: str,
    question: str,
    answer: str,
    rating: str,
    response_id: str = "",
    reason: str = "",
    resolved: bool | None = None,
    comment: str = "",
) -> bool:
    """Enregistre un retour une seule fois par réponse et par session.

    Appelée sans condition de consentement : cliquer sur ce bouton est en
    lui-même l'accord explicite de l'utilisateur pour cette seule réponse.
    """
    if reason not in ALLOWED_FEEDBACK_REASONS:
        reason = "other"
    postgres = _is_postgres(settings.analytics_database_url)
    placeholder = "%s" if postgres else "?"
    columns = (
        "response_id", "session_id", "language", "question", "answer",
        "rating", "reason", "resolved", "comment",
    )
    statement = (
        f"INSERT INTO answer_feedback ({', '.join(columns)}) VALUES "
        f"({', '.join([placeholder] * len(columns))})"
    )
    if response_id:
        if postgres:
            statement += " ON CONFLICT DO NOTHING"
        else:
            statement = statement.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
    with _connection(settings) as conn:
        cursor = conn.execute(
            statement,
            (
                response_id or None, pseudonymize_session(settings, session_id), language,
                question, answer, rating, reason, resolved, comment,
            ),
        )
        conn.commit()
        return cursor.rowcount != 0


def log_ui_event(
    settings: Settings,
    *,
    event_type: str,
    session_id: str,
    language: str,
    response_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Conserve un événement de parcours explicitement autorisé."""
    if event_type not in ALLOWED_UI_EVENTS:
        raise ValueError("Type d'événement analytics non autorisé.")
    placeholder = "%s" if _is_postgres(settings.analytics_database_url) else "?"
    statement = (
        "INSERT INTO ui_events "
        "(event_type, session_id, response_id, language, metadata_json) "
        f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})"
    )
    safe_metadata = json.dumps(
        metadata or {}, ensure_ascii=False, separators=(",", ":")
    )[:2000]
    with _connection(settings) as conn:
        conn.execute(
            statement,
            (
                event_type, pseudonymize_session(settings, session_id),
                response_id or None, language, safe_metadata,
            ),
        )
        conn.commit()


def _recent(created_at: Any, days: int) -> bool:
    """Filtre en Python pour garder la même logique SQLite/PostgreSQL."""
    if isinstance(created_at, datetime):
        value = created_at
    else:
        try:
            value = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        except ValueError:
            return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value >= datetime.now(timezone.utc) - timedelta(days=days)


def _percent(numerator: int, denominator: int) -> float | None:
    return round(numerator * 100 / denominator, 1) if denominator else None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(round((len(ordered) - 1) * fraction), len(ordered) - 1)
    return round(ordered[index], 1)


def analytics_snapshot(settings: Settings, days: int = 30) -> dict[str, Any]:
    """Construit les KPI du pilote sans exposer le texte des conversations."""
    ensure_schema(settings)
    with _connection(settings) as conn:
        interactions = conn.execute(
            "SELECT response_id, session_id, language, topic, status, grounded, "
            "clarification_needed, source_count, latency_ms, input_tokens, "
            "cached_input_tokens, output_tokens, reasoning_tokens, total_tokens, "
            "provider, model, fallback_used, sources_json, created_at "
            "FROM logged_questions"
        ).fetchall()
        feedback = conn.execute(
            "SELECT response_id, rating, reason, resolved, language, created_at "
            "FROM answer_feedback"
        ).fetchall()
        events = conn.execute(
            "SELECT event_type, session_id, language, created_at FROM ui_events"
        ).fetchall()

    interactions = [row for row in interactions if _recent(row[18], days)]
    feedback = [row for row in feedback if _recent(row[5], days)]
    events = [row for row in events if _recent(row[3], days)]
    sessions = {row[1] for row in interactions}
    interaction_ids = {str(row[0]) for row in interactions if row[0]}
    rated_interaction_ids = {
        str(row[0]) for row in feedback if row[0] and str(row[0]) in interaction_ids
    }
    latencies = [float(row[8]) for row in interactions if row[8] is not None]
    ratings = Counter(str(row[1]) for row in feedback)
    statuses = Counter(str(row[4] or "legacy") for row in interactions)
    languages = Counter(str(row[2]) for row in interactions)
    topics = Counter(str(row[3] or "non_classe") for row in interactions)
    providers = Counter(
        f"{row[14] or 'inconnu'} / {row[15] or 'inconnu'}" for row in interactions
    )
    daily = Counter(str(row[18])[:10] for row in interactions)
    event_counts = Counter(str(row[0]) for row in events)
    source_counts: Counter[str] = Counter()
    for row in interactions:
        try:
            sources = json.loads(row[17] or "[]")
        except json.JSONDecodeError:
            sources = []
        for source in sources:
            label = str(
                source.get("citation") or source.get("source_title") or "source"
            )
            source_counts[label] += 1
    resolved_values = [bool(row[3]) for row in feedback if row[3] is not None]
    grounding_values = [bool(row[5]) for row in interactions if row[5] is not None]
    clarification_values = [bool(row[6]) for row in interactions if row[6] is not None]
    fallback_values = [bool(row[16]) for row in interactions if row[16] is not None]
    token_measured = [row for row in interactions if row[13] is not None]
    total_tokens = sum(int(row[13] or 0) for row in token_measured)
    overview = {
        "consented_sessions": len(sessions),
        "interactions": len(interactions),
        "questions_per_session": round(len(interactions) / len(sessions), 2)
        if sessions else 0,
        "feedback_count": len(feedback),
        "feedback_coverage_pct": _percent(
            len(rated_interaction_ids), len(interaction_ids)
        ),
        "satisfaction_pct": _percent(ratings["up"], sum(ratings.values())),
        "resolution_pct": _percent(sum(resolved_values), len(resolved_values)),
        "grounded_pct": _percent(sum(grounding_values), len(grounding_values)),
        "clarification_pct": _percent(
            sum(clarification_values), len(clarification_values)
        ),
        "fallback_pct": _percent(sum(fallback_values), len(fallback_values)),
        "latency_p50_ms": round(median(latencies), 1) if latencies else None,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "total_tokens": total_tokens,
        "token_measurement_coverage_pct": _percent(
            len(token_measured), len(interactions)
        ),
        "tokens_per_interaction": round(total_tokens / len(token_measured), 1)
        if token_measured else None,
    }
    quality_by_language: dict[str, dict[str, Any]] = {}
    for language in sorted(languages):
        language_interactions = [row for row in interactions if row[2] == language]
        language_feedback = [
            row
            for row in feedback
            if row[4] == language and row[0] and str(row[0]) in interaction_ids
        ]
        language_latencies = [
            float(row[8]) for row in language_interactions if row[8] is not None
        ]
        positive = sum(row[1] == "up" for row in language_feedback)
        quality_by_language[language] = {
            "interactions": len(language_interactions),
            "feedback": len(language_feedback),
            "satisfaction_pct": _percent(positive, len(language_feedback)),
            "grounded_pct": _percent(
                sum(bool(row[5]) for row in language_interactions if row[5] is not None),
                sum(row[5] is not None for row in language_interactions),
            ),
            "latency_p50_ms": (
                round(median(language_latencies), 1) if language_latencies else None
            ),
        }
    insights: list[str] = []
    if len(interactions) < 30:
        insights.append(
            "Échantillon insuffisant pour une conclusion client : viser au moins 30 interactions consenties avant d'interpréter les taux."
        )
    feedback_coverage = overview["feedback_coverage_pct"]
    if feedback_coverage is None:
        insights.append(
            "La couverture du feedback n'est pas encore mesurable sur les anciennes lignes ; elle commencera avec les nouveaux response_id."
        )
    elif feedback_coverage < 15:
        insights.append(
            "Moins de 15 % des réponses sont évaluées : rendre l'invitation au feedback plus visible avant de conclure sur la satisfaction."
        )
    if overview["satisfaction_pct"] is not None and overview["satisfaction_pct"] < 75:
        insights.append(
            "La satisfaction est inférieure à 75 % : examiner d'abord les motifs incorrect, incomplet et source manquante."
        )
    if overview["grounded_pct"] is not None and overview["grounded_pct"] < 90:
        insights.append(
            "Moins de 90 % des interactions sont sourcées : analyser séparément les refus légitimes, les clarifications et les échecs de retrieval."
        )
    if overview["clarification_pct"] is not None and overview["clarification_pct"] > 20:
        insights.append(
            "Plus d'une interaction sur cinq demande une clarification : enrichir les synonymes et les formulations suggérées des thèmes concernés."
        )
    if overview["fallback_pct"] is not None and overview["fallback_pct"] > 5:
        insights.append(
            "Le taux de repli modèle dépasse 5 % : contrôler quotas, timeouts et erreurs par fournisseur avant une généralisation."
        )
    if overview["latency_p95_ms"] is not None and overview["latency_p95_ms"] > 10000:
        insights.append(
            "La latence p95 dépasse 10 secondes : ventiler model_calls entre planification, reranking et génération pour cibler l'étape lente."
        )
    if topics:
        top_topic, top_count = topics.most_common(1)[0]
        insights.append(
            f"Le thème dominant est « {top_topic} » ({top_count} interaction(s)) : prioriser sa couverture documentaire et ses tests métier."
        )
    return {
        "period_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overview": overview,
        "tokens": {
            "input": sum(int(row[9] or 0) for row in token_measured),
            "cached_input": sum(int(row[10] or 0) for row in token_measured),
            "output": sum(int(row[11] or 0) for row in token_measured),
            "reasoning": sum(int(row[12] or 0) for row in token_measured),
            "total": total_tokens,
        },
        "by_language": dict(languages.most_common()),
        "quality_by_language": quality_by_language,
        "by_day": dict(sorted(daily.items())),
        "by_topic": dict(topics.most_common()),
        "by_status": dict(statuses.most_common()),
        "by_provider_model": dict(providers.most_common()),
        "feedback_reasons": dict(
            Counter(str(row[2] or "sans_raison") for row in feedback).most_common()
        ),
        "ui_events": dict(event_counts.most_common()),
        "top_sources": dict(source_counts.most_common(10)),
        "insights": insights,
    }


def content_gap_examples(
    settings: Settings, days: int = 30, limit: int = 20
) -> list[dict[str, str]]:
    """Retourne des questions consenties à examiner manuellement.

    Cette fonction est volontairement séparée de ``analytics_snapshot`` : le
    dashboard agrégé n'expose jamais le texte. L'export nominatif doit être
    déclenché explicitement par l'analyste et traité selon la durée de
    conservation annoncée aux utilisateurs.
    """
    ensure_schema(settings)
    with _connection(settings) as conn:
        questions = conn.execute(
            "SELECT response_id, question, status, topic, created_at "
            "FROM logged_questions"
        ).fetchall()
        feedback = conn.execute(
            "SELECT response_id, rating, reason, created_at FROM answer_feedback"
        ).fetchall()
    negative = {
        str(row[0]): str(row[2] or "down")
        for row in feedback
        if row[0] and row[1] == "down" and _recent(row[3], days)
    }
    examples: list[dict[str, str]] = []
    for response_id, question, status, topic, created_at in questions:
        if not _recent(created_at, days):
            continue
        response_key = str(response_id or "")
        if status in {"answered", "answered_degraded"} and response_key not in negative:
            continue
        examples.append(
            {
                "question": str(question),
                "status": str(status or "legacy"),
                "topic": str(topic or "non_classe"),
                "feedback_reason": negative.get(response_key, ""),
            }
        )
        if len(examples) >= max(1, min(limit, 100)):
            break
    return examples
