"""Inventaire du corpus transmis au générateur avec les extraits."""

from api import providers
from api.app import create_app
from api.providers import _context, format_inventaire, stream_answer
from core.config import get_settings

MANIFEST = [
    {"source_type": "pdf", "title": "Rapport annuel BCM - exercice 2025"},
    {"source_type": "lettre", "title": "Lettre d'information de la BCM — Août 2026"},
    {"source_type": "lettre", "title": "Lettre d'information de la BCM — Septembre 2026"},
]


def test_inventory_lists_every_indexed_edition() -> None:
    text = format_inventaire(MANIFEST)
    assert text.startswith("--- INVENTAIRE DU CORPUS")
    assert "- Rapport annuel BCM - exercice 2025" in text
    assert "- Lettres d'information (2 éditions) : Août 2026, Septembre 2026" in text


def test_empty_manifest_gives_no_inventory() -> None:
    assert format_inventaire([]) == ""
    assert "INVENTAIRE" not in _context([], format_inventaire([]))


def test_context_places_inventory_before_extracts() -> None:
    text = _context(
        [{"text": "Texte", "source_type": "pdf", "pdf_page": 3}],
        format_inventaire(MANIFEST),
    )
    assert text.index("INVENTAIRE DU CORPUS") < text.index("EXTRAIT 1")


def test_non_streaming_provider_receives_the_inventory(monkeypatch) -> None:
    recu = {}

    def fausse_generation(question, results, history, settings, language, quality, inventaire=""):
        recu["inventaire"] = inventaire
        return "Réponse"

    monkeypatch.setattr(providers, "generate_gemini", fausse_generation)
    list(stream_answer("gemini", "question", [], [], inventaire="INVENTAIRE X"))
    assert recu["inventaire"] == "INVENTAIRE X"


def test_api_sends_the_loaded_index_inventory(monkeypatch) -> None:
    recu = {}

    def flux(provider, question, results, history, settings=None, language=None,
             quality=None, inventaire=""):
        recu["inventaire"] = inventaire
        yield ("delta", "Réponse [p. PDF 21].")
        yield ("final", "Réponse [p. PDF 21].")

    monkeypatch.setattr("api.app.stream_answer", flux)
    settings = get_settings(
        {"APP_ENV": "test", "GENERATION_PROVIDER": "extractive", "OPEN_BROWSER": "false"}
    )
    client = create_app(settings_override=settings).test_client()
    client.post(
        "/api/ask/stream",
        json={"question": "Quel est le taux de croissance du PIB réel en 2025 ?"},
    ).get_data()
    assert "INVENTAIRE DU CORPUS" in recu["inventaire"]
    assert "Rapport annuel" in recu["inventaire"]
