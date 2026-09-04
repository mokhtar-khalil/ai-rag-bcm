"""Lecture et validation centralisées de la configuration du chatbot BCM."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)


class ConfigurationError(ValueError):
    """Erreur de configuration explicite et sûre à afficher au démarrage."""


def _text(env: Mapping[str, str], name: str, default: str) -> str:
    """Lit une variable texte en supprimant les espaces superflus."""
    return str(env.get(name, default)).strip()


def _integer(
    env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    """Lit un entier et vérifie qu'il reste dans l'intervalle autorisé."""
    raw = _text(env, name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} doit être un entier, valeur reçue : {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{name} doit être compris entre {minimum} et {maximum}, valeur reçue : {value}"
        )
    return value


def _decimal(
    env: Mapping[str, str], name: str, default: float, minimum: float, maximum: float
) -> float:
    """Lit un nombre décimal et vérifie ses bornes fonctionnelles."""
    raw = _text(env, name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} doit être un nombre, valeur reçue : {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{name} doit être compris entre {minimum} et {maximum}, valeur reçue : {value}"
        )
    return value


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    """Accepte les représentations usuelles françaises et anglaises d'un booléen."""
    raw = _text(env, name, "true" if default else "false").casefold()
    if raw in {"1", "true", "yes", "oui", "on"}:
        return True
    if raw in {"0", "false", "no", "non", "off"}:
        return False
    raise ConfigurationError(f"{name} doit être true ou false, valeur reçue : {raw!r}")


def _path(env: Mapping[str, str], name: str, default: Path) -> Path:
    """Résout un chemin relatif par rapport à la racine du projet."""
    raw = _text(env, name, str(default))
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _list(env: Mapping[str, str], name: str, default: str = "") -> tuple[str, ...]:
    """Lit une liste d'origines/valeurs séparées par des virgules."""
    raw = _text(env, name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    """Regroupe les paramètres validés et immuables utilisés par l'application."""
    app_env: str
    log_level: str
    log_dir: Path
    report_path: Path
    index_path: Path
    api_host: str
    api_port: int
    api_url: str
    gradio_host: str
    gradio_port: int
    widget_host: str
    widget_port: int
    open_browser: bool
    generation_provider: str
    openai_api_key: str = field(repr=False)
    openai_model: str
    openai_rerank_model: str
    openai_reasoning_effort: str
    openai_max_output_tokens: int
    gemini_api_key: str = field(repr=False)
    gemini_model: str
    gemini_max_output_tokens: int
    gemini_thinking_level: str
    embedding_model: str
    embedding_cache_path: Path
    semantic_retrieval: bool
    semantic_weight: float
    min_semantic_score: float
    embedding_batch_size: int
    chart_analysis_enabled: bool
    chart_cache_path: Path
    chart_render_dpi: int
    chart_max_pages: int
    chart_ocr_max_chars: int
    chart_ocr_path: Path
    pdf_renderer_path: str
    ollama_base_url: str
    ollama_model: str
    top_k: int
    retrieval_candidates: int
    min_relevance_score: float
    max_question_chars: int
    max_history_messages: int
    max_request_bytes: int
    reindex_token: str = field(repr=False)
    cors_allowed_origins: tuple[str, ...]
    rate_limit_ask: str
    session_max_questions: int
    session_idle_minutes: int
    analytics_database_url: str = field(repr=False)
    analytics_hash_salt: str = field(repr=False)
    analytics_admin_token: str = field(repr=False)

    @property
    def widget_origin(self) -> str:
        """Origine exacte que le navigateur enverra depuis la page du widget."""
        return f"http://{self.widget_host}:{self.widget_port}"

    @property
    def is_production(self) -> bool:
        """Vaut vrai lorsque l'application fonctionne en production."""
        return self.app_env == "production"

    @property
    def is_testing(self) -> bool:
        """Vaut vrai lorsque les comportements réservés aux tests sont actifs."""
        return self.app_env == "test"


def get_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Construit la configuration depuis l'environnement ou un dictionnaire injecté."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    app_env = _text(env, "APP_ENV", "development").casefold()
    if app_env not in {"development", "test", "production"}:
        raise ConfigurationError("APP_ENV doit être development, test ou production.")

    provider = _text(env, "GENERATION_PROVIDER", "auto").casefold()
    if provider not in {"auto", "openai", "gemini", "ollama", "extractive"}:
        raise ConfigurationError(
            "GENERATION_PROVIDER doit être auto, openai, gemini, ollama ou extractive."
        )

    openai_api_key = _text(env, "OPENAI_API_KEY", "")
    if provider == "openai" and not openai_api_key and app_env == "production":
        raise ConfigurationError(
            "OPENAI_API_KEY est requis en production lorsque GENERATION_PROVIDER vaut openai."
        )

    gemini_api_key = _text(env, "GEMINI_API_KEY", "")
    if provider == "gemini" and not gemini_api_key and app_env == "production":
        raise ConfigurationError(
            "GEMINI_API_KEY est requis en production lorsque GENERATION_PROVIDER vaut gemini."
        )

    thinking_level = _text(env, "GEMINI_THINKING_LEVEL", "low").casefold()
    if thinking_level not in {"", "low", "medium", "high"}:
        raise ConfigurationError(
            "GEMINI_THINKING_LEVEL doit valoir low, medium, high, ou rester vide."
        )

    top_k = _integer(env, "TOP_K", 5, 1, 10)
    # Le budget de candidats était calibré sur un corpus d'un seul document. Le
    # corpus réunit désormais le rapport annuel et les Lettres d'information :
    # à budget constant, les passages d'une source évincent ceux de l'autre.
    retrieval_candidates = _integer(env, "RETRIEVAL_CANDIDATES", 18, top_k, 50)
    api_host = _text(env, "API_HOST", "127.0.0.1")
    # Les plateformes de type Railway ou Heroku imposent le port d'écoute par la
    # variable PORT, choisie au démarrage du conteneur. Elle prime donc sur
    # API_PORT, qui reste la valeur de référence en local et en Docker.
    api_port = _integer(env, "PORT", _integer(env, "API_PORT", 5000, 1, 65535), 1, 65535)
    return Settings(
        app_env=app_env,
        log_level=_text(env, "LOG_LEVEL", "INFO").upper(),
        log_dir=_path(env, "LOG_DIR", PROJECT_ROOT / "logs"),
        report_path=_path(
            env, "REPORT_PATH", PROJECT_ROOT / "data" / "Rapport annuel 2025-BCM.pdf"
        ),
        index_path=_path(env, "INDEX_PATH", PROJECT_ROOT / "storage" / "bcm_index.joblib"),
        api_host=api_host,
        api_port=api_port,
        api_url=_text(env, "API_URL", f"http://{api_host}:{api_port}").rstrip("/"),
        gradio_host=_text(env, "GRADIO_HOST", "127.0.0.1"),
        gradio_port=_integer(env, "GRADIO_PORT", 7861, 1, 65535),
        # Serveur statique local qui sert le widget et sa page de démonstration.
        # Son origine doit figurer dans CORS_ALLOWED_ORIGINS, sinon le
        # navigateur bloque tous les appels du widget vers l'API.
        widget_host=_text(env, "WIDGET_HOST", "127.0.0.1"),
        widget_port=_integer(env, "WIDGET_PORT", 8090, 1, 65535),
        open_browser=_boolean(env, "OPEN_BROWSER", True),
        generation_provider=provider,
        openai_api_key=openai_api_key,
        openai_model=_text(env, "OPENAI_MODEL", "gpt-5.6-terra"),
        openai_rerank_model=_text(env, "OPENAI_RERANK_MODEL", "gpt-5.6-luna"),
        openai_reasoning_effort=_text(env, "OPENAI_REASONING_EFFORT", "medium"),
        # Comme chez Gemini, les tokens de raisonnement sont décomptés de ce
        # budget. Mesuré ici : 184 tokens de raisonnement pour 572 de réponse
        # sur une question large, et jusqu'à 120 pour un reranking qui n'en
        # produit que 18. Un plafond serré ne réduit pas le coût — seuls les
        # tokens réellement produits sont facturés — mais tronque la sortie.
        openai_max_output_tokens=_integer(
            env, "OPENAI_MAX_OUTPUT_TOKENS", 3000, 500, 32000
        ),
        gemini_api_key=gemini_api_key,
        gemini_model=_text(env, "GEMINI_MODEL", "gemini-3.6-flash"),
        # Les modèles Gemini récents raisonnent avant de répondre et leurs
        # tokens de réflexion sont décomptés de ce budget. Un budget calibré sur
        # la seule longueur de la réponse la fait couper en plein milieu.
        # Mesuré sur ce corpus : la réflexion consomme de 350 à plus de 3 500
        # tokens selon la question, de façon peu prévisible. Le plafond n'est
        # facturé que s'il est consommé : le fixer haut ne coûte rien et évite
        # de servir une réponse coupée.
        gemini_max_output_tokens=_integer(
            env, "GEMINI_MAX_OUTPUT_TOKENS", 8000, 500, 32000
        ),
        # « low » divise la latence par cinq sur une question large (36 s -> 7 s)
        # pour une réponse de longueur et de structure comparables. Une réponse
        # documentaire est fondée sur les extraits fournis : elle exige peu de
        # raisonnement autonome. Mettre "" pour laisser le modèle décider.
        gemini_thinking_level=_text(env, "GEMINI_THINKING_LEVEL", "low").casefold(),
        embedding_model=_text(env, "EMBEDDING_MODEL", "intfloat/multilingual-e5-small"),
        embedding_cache_path=_path(
            env, "EMBEDDING_CACHE_PATH", PROJECT_ROOT / "storage" / "models"
        ),
        semantic_retrieval=_boolean(env, "SEMANTIC_RETRIEVAL", app_env != "test"),
        semantic_weight=_decimal(env, "SEMANTIC_WEIGHT", 0.45, 0.0, 1.0),
        min_semantic_score=_decimal(env, "MIN_SEMANTIC_SCORE", 0.88, 0.0, 1.0),
        embedding_batch_size=_integer(env, "EMBEDDING_BATCH_SIZE", 64, 1, 256),
        chart_analysis_enabled=_boolean(
            env, "CHART_ANALYSIS_ENABLED", app_env != "test"
        ),
        chart_cache_path=_path(
            env, "CHART_CACHE_PATH", PROJECT_ROOT / "storage" / "chart_pages"
        ),
        chart_render_dpi=_integer(env, "CHART_RENDER_DPI", 170, 100, 240),
        chart_max_pages=_integer(env, "CHART_MAX_PAGES", 2, 1, 3),
        chart_ocr_max_chars=_integer(
            env, "CHART_OCR_MAX_CHARS", 5000, 1000, 12000
        ),
        chart_ocr_path=_path(
            env, "CHART_OCR_PATH", PROJECT_ROOT / "storage" / "chart_ocr"
        ),
        pdf_renderer_path=_text(env, "PDF_RENDERER_PATH", ""),
        ollama_base_url=_text(env, "OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=_text(env, "OLLAMA_MODEL", "llama3.2:3b"),
        top_k=top_k,
        retrieval_candidates=retrieval_candidates,
        min_relevance_score=_decimal(env, "MIN_RELEVANCE_SCORE", 0.075, 0.0, 1.0),
        max_question_chars=_integer(env, "MAX_QUESTION_CHARS", 2000, 100, 20000),
        max_history_messages=_integer(env, "MAX_HISTORY_MESSAGES", 16, 0, 50),
        max_request_bytes=_integer(env, "MAX_REQUEST_BYTES", 65536, 1024, 1048576),
        reindex_token=_text(env, "REINDEX_TOKEN", ""),
        cors_allowed_origins=_list(env, "CORS_ALLOWED_ORIGINS"),
        rate_limit_ask=_text(env, "RATE_LIMIT_ASK", "20 per minute"),
        # Anti-abus par session, distinct de RATE_LIMIT_ASK : celui-ci freine un
        # débit trop rapide, celui-là plafonne le volume total d'une session
        # avant réinitialisation par inactivité.
        session_max_questions=_integer(env, "SESSION_MAX_QUESTIONS", 10, 1, 1000),
        session_idle_minutes=_integer(env, "SESSION_IDLE_MINUTES", 30, 1, 1440),
        # Journalisation des questions à des fins d'analyse, soumise au
        # consentement transmis par le client. Une URL Postgres (fournie par
        # exemple par l'extension Railway) active la persistance ; à défaut,
        # un fichier SQLite local sert de repli pour le développement — non
        # durable sur Railway sans volume attaché.
        analytics_database_url=_text(env, "DATABASE_URL", ""),
        # Secret distinct des clés fournisseur. Il sert uniquement à rendre
        # l'identifiant aléatoire du navigateur non réversible dans la base.
        analytics_hash_salt=_text(env, "ANALYTICS_HASH_SALT", ""),
        # Protège l'endpoint d'agrégats destiné au dashboard central.
        analytics_admin_token=_text(env, "ANALYTICS_ADMIN_TOKEN", ""),
    )
