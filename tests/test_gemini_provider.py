"""Tests du fournisseur Gemini, bascule temporaire pendant un quota OpenAI épuisé."""

from types import SimpleNamespace

import pytest

from api.providers import answer_with_provider, generate_gemini
from core.config import get_settings


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return SimpleNamespace(text=self._text)


def _fake_client_class(models: _FakeModels):
    class FakeClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.models = models

    return FakeClient


def test_generate_gemini_returns_grounded_answer(monkeypatch) -> None:
    models = _FakeModels("La croissance a été de 4,0 % [p. PDF 5].")
    monkeypatch.setattr("google.genai.Client", _fake_client_class(models))
    settings = get_settings(
        {"APP_ENV": "test", "GENERATION_PROVIDER": "gemini", "GEMINI_API_KEY": "fake-key"}
    )
    answer = generate_gemini(
        "Quel a été le taux de croissance ?",
        [{"chunk_id": 1, "pdf_page": 5, "text": "La croissance...", "score": 0.5}],
        [],
        settings,
    )
    assert "[p. PDF 5]" in answer
    assert models.calls[0]["model"] == "gemini-2.5-flash"


def test_generate_gemini_rejects_invalid_citation(monkeypatch) -> None:
    models = _FakeModels("Réponse hors sources [p. PDF 99].")
    monkeypatch.setattr("google.genai.Client", _fake_client_class(models))
    settings = get_settings(
        {"APP_ENV": "test", "GENERATION_PROVIDER": "gemini", "GEMINI_API_KEY": "fake-key"}
    )
    with pytest.raises(ValueError):
        generate_gemini(
            "Question",
            [{"chunk_id": 1, "pdf_page": 5, "text": "...", "score": 0.5}],
            [],
            settings,
        )


def test_answer_with_provider_routes_to_gemini(monkeypatch) -> None:
    monkeypatch.setattr(
        "api.providers.generate_gemini",
        lambda question, results, history, settings=None, language=None: "réponse gemini",
    )
    assert (
        answer_with_provider("gemini", "question", [], [], None, None) == "réponse gemini"
    )
