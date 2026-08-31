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

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

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
    """Crée la table de journalisation si elle n'existe pas encore."""
    statement = (
        CREATE_TABLE_POSTGRES
        if _is_postgres(settings.analytics_database_url)
        else CREATE_TABLE_SQLITE
    )
    with _connection(settings) as conn:
        conn.execute(statement)
        conn.commit()


def log_question(
    settings: Settings, session_id: str, language: str, question: str, answer: str
) -> None:
    """Enregistre une question et sa réponse.

    Le tri entre consentement donné ou non se fait avant l'appel : cette
    fonction journalise inconditionnellement ce qu'on lui transmet.
    """
    placeholder = "%s" if _is_postgres(settings.analytics_database_url) else "?"
    statement = (
        "INSERT INTO logged_questions (session_id, language, question, answer) "
        f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})"
    )
    with _connection(settings) as conn:
        conn.execute(statement, (session_id, language, question, answer))
        conn.commit()
