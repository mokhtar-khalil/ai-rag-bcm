"""Mesure locale des appels aux modèles pour l'analytics du pilote.

Le collecteur est attaché au contexte courant avec ``contextvars`` : deux
requêtes simultanées ne mélangent donc jamais leurs compteurs. Les objets de
réponse des fournisseurs restent privés ; seules les métriques techniques
(modèle, opération, latence et tokens) sont conservées.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from time import perf_counter
from typing import Any


_calls: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "bcm_model_usage_calls", default=None
)


def begin_usage_collection() -> Token:
    """Démarre une collecte isolée et retourne le jeton de restauration."""
    return _calls.set([])


def reset_usage_collection(token: Token) -> None:
    """Restaure le contexte précédent à la fin de la requête."""
    _calls.reset(token)


def usage_snapshot() -> list[dict[str, Any]]:
    """Retourne une copie sérialisable des appels observés."""
    return [dict(item) for item in (_calls.get() or [])]


def model_call_started() -> float:
    """Retourne un repère monotone à passer aux fonctions d'enregistrement."""
    return perf_counter()


def _integer(value: Any) -> int:
    """Convertit les compteurs optionnels des SDK sans jamais lever d'erreur."""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _field(value: Any, name: str, default: Any = 0) -> Any:
    """Lit indifféremment un attribut de SDK ou une clé de dictionnaire."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _usage_counts(provider: str, response: Any) -> dict[str, int]:
    """Normalise les formats OpenAI, Gemini et Ollama."""
    if provider == "openai":
        usage = _field(response, "usage", None)
        input_details = _field(usage, "input_tokens_details", None)
        output_details = _field(usage, "output_tokens_details", None)
        return {
            "input_tokens": _integer(_field(usage, "input_tokens")),
            "cached_input_tokens": _integer(_field(input_details, "cached_tokens")),
            "output_tokens": _integer(_field(usage, "output_tokens")),
            "reasoning_tokens": _integer(_field(output_details, "reasoning_tokens")),
            "total_tokens": _integer(_field(usage, "total_tokens")),
        }
    if provider == "gemini":
        usage = _field(response, "usage_metadata", None)
        return {
            "input_tokens": _integer(_field(usage, "prompt_token_count")),
            "cached_input_tokens": _integer(
                _field(usage, "cached_content_token_count")
            ),
            "output_tokens": _integer(_field(usage, "candidates_token_count")),
            "reasoning_tokens": _integer(_field(usage, "thoughts_token_count")),
            "total_tokens": _integer(_field(usage, "total_token_count")),
        }
    if provider == "ollama":
        prompt = _integer(_field(response, "prompt_eval_count"))
        output = _integer(_field(response, "eval_count"))
        return {
            "input_tokens": prompt,
            "cached_input_tokens": 0,
            "output_tokens": output,
            "reasoning_tokens": 0,
            "total_tokens": prompt + output,
        }
    return {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }


def record_model_response(
    operation: str,
    provider: str,
    model: str,
    response: Any,
    started_at: float,
) -> None:
    """Ajoute un appel réussi avec sa consommation déclarée par le SDK."""
    calls = _calls.get()
    if calls is None:
        return
    counts = _usage_counts(provider, response)
    if not counts["total_tokens"]:
        counts["total_tokens"] = counts["input_tokens"] + counts["output_tokens"]
    calls.append(
        {
            "operation": operation,
            "provider": provider,
            "model": model,
            **counts,
            "latency_ms": round((perf_counter() - started_at) * 1000, 1),
            "success": True,
            "error_type": "",
        }
    )


def record_model_error(
    operation: str,
    provider: str,
    model: str,
    error: Exception,
    started_at: float,
) -> None:
    """Conserve un échec sans enregistrer son message potentiellement sensible."""
    calls = _calls.get()
    if calls is None:
        return
    if calls and all(
        (
            calls[-1].get("operation") == operation,
            calls[-1].get("provider") == provider,
            calls[-1].get("model") == model,
            calls[-1].get("success") is False,
            calls[-1].get("error_type") == type(error).__name__,
        )
    ):
        return
    calls.append(
        {
            "operation": operation,
            "provider": provider,
            "model": model,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "latency_ms": round((perf_counter() - started_at) * 1000, 1),
            "success": False,
            "error_type": type(error).__name__,
        }
    )
