"""Vérifie la diffusion de la réponse au fil de sa rédaction (Server-Sent Events)."""

from __future__ import annotations

import json

from api.app import create_app
from core.config import get_settings
from tests.conftest import generation_diffusee


def settings_de_test():
    """Réglages déterministes, sans appel à un service distant."""
    return get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "OPEN_BROWSER": "false",
        }
    )


def evenements(response) -> list[tuple[str, dict]]:
    """Décode le corps SSE en une liste ordonnée d'événements."""
    decodes: list[tuple[str, dict]] = []
    for trame in response.get_data(as_text=True).split("\n\n"):
        nom, donnees = "", ""
        for ligne in trame.split("\n"):
            if ligne.startswith("event: "):
                nom = ligne[7:].strip()
            elif ligne.startswith("data: "):
                donnees += ligne[6:]
        if nom and donnees:
            decodes.append((nom, json.loads(donnees)))
    return decodes


def test_stream_returns_an_event_stream() -> None:
    client = create_app(settings_override=settings_de_test()).test_client()
    response = client.post(
        "/api/ask/stream",
        json={"question": "Quel est le taux de croissance du PIB réel en 2025 ?"},
    )
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    # Sans cet en-tête, un proxy comme nginx tamponne la réponse et annule le
    # bénéfice de la diffusion.
    assert response.headers["X-Accel-Buffering"] == "no"


def test_stream_announces_its_stages_before_the_first_word() -> None:
    """L'attente initiale doit être nommée : plusieurs secondes la séparent du texte."""
    client = create_app(settings_override=settings_de_test()).test_client()
    response = client.post(
        "/api/ask/stream",
        json={"question": "Quel est le taux de croissance du PIB réel en 2025 ?"},
    )
    noms = [nom for nom, _ in evenements(response)]
    assert noms[0] == "stage"
    assert "delta" in noms
    assert noms[-1] == "done"
    etapes = [
        charge["stage"] for nom, charge in evenements(response) if nom == "stage"
    ]
    assert etapes[0] == "recherche"
    assert "redaction" in etapes


def test_streamed_answer_matches_the_single_call_endpoint() -> None:
    """Les deux points d'entrée partagent le pipeline : ils ne doivent pas diverger."""
    client = create_app(settings_override=settings_de_test()).test_client()
    question = {"question": "Quel est le taux de croissance du PIB réel en 2025 ?"}

    complet = client.post("/api/ask", json=question).json
    diffuse = [
        charge
        for nom, charge in evenements(client.post("/api/ask/stream", json=question))
        if nom == "done"
    ][0]

    assert diffuse["answer"] == complet["answer"]
    assert diffuse["grounded"] == complet["grounded"]
    assert [s["pdf_page"] for s in diffuse["sources"]] == [
        s["pdf_page"] for s in complet["sources"]
    ]


def test_streamed_fragments_are_provisional_until_the_final_event(monkeypatch) -> None:
    """Le texte diffusé n'a pas passé les contrôles : « done » fait autorité."""

    def fausse_generation(provider, question, results, history, settings, language):
        return "Réponse validée [p. PDF 21]."

    monkeypatch.setattr(
        "api.app.stream_answer", generation_diffusee(fausse_generation)
    )
    client = create_app(settings_override=settings_de_test()).test_client()
    decodes = evenements(
        client.post(
            "/api/ask/stream",
            json={"question": "Quel est le taux de croissance du PIB réel en 2025 ?"},
        )
    )
    fragments = "".join(
        charge["text"] for nom, charge in decodes if nom == "delta"
    )
    final = [charge for nom, charge in decodes if nom == "done"][0]
    assert fragments == "Réponse validée [p. PDF 21]."
    assert final["answer"] == "Réponse validée [p. PDF 21]."


def test_stream_reports_an_invalid_request_as_an_error_event() -> None:
    """Une requête invalide ne doit pas rester silencieuse côté client."""
    client = create_app(settings_override=settings_de_test()).test_client()
    decodes = evenements(client.post("/api/ask/stream", json={"question": "   "}))
    assert [nom for nom, _ in decodes] == ["error"]
    assert decodes[0][1]["status"] == 400


def test_stream_refuses_an_out_of_corpus_question() -> None:
    """Le garde-fou documentaire s'applique aussi à la voie diffusée."""
    client = create_app(settings_override=settings_de_test()).test_client()
    decodes = evenements(
        client.post(
            "/api/ask/stream",
            json={"question": "Quelle est la recette traditionnelle des sushis ?"},
        )
    )
    final = [charge for nom, charge in decodes if nom == "done"][0]
    assert final["grounded"] is False
    assert "documents BCM" in final["answer"]
