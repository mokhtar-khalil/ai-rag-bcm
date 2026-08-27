from __future__ import annotations

import os

import pytest


os.environ["APP_ENV"] = "test"
os.environ["GENERATION_PROVIDER"] = "extractive"
os.environ["OPEN_BROWSER"] = "false"


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empêche les tests d'appeler un service externe ou de dépendre de la clé locale."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GENERATION_PROVIDER", "extractive")
    monkeypatch.setenv("OPEN_BROWSER", "false")


def generation_diffusee(fausse_generation):
    """Adapte une génération simulée à la forme diffusée attendue par l'API.

    Le point d'entrée de la génération est devenu `stream_answer`, qui produit
    des fragments puis la réponse finalisée. Cet adaptateur laisse les tests
    décrire la réponse attendue par une simple fonction, comme auparavant.
    """

    def flux(provider, question, results, history, settings=None, language=None):
        reponse = fausse_generation(
            provider, question, results, history, settings, language
        )
        yield ("delta", reponse)
        yield ("final", reponse)

    return flux
