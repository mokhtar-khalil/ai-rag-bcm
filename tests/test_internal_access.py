"""Vérifie la page interne et le widget servis directement par l'API Flask.

Ce service assure désormais lui-même ce qu'un hébergeur statique séparé
faisait auparavant (voir docs/ACCES_INTERNE_BCM.md) : une seule origine pour
la page, le script du widget et l'API, sans CORS à négocier entre deux
plateformes.
"""

from __future__ import annotations

import base64

from api.app import create_app
from core.config import get_settings


def test_internal_page_is_open_by_default() -> None:
    """Sans identifiants configurés, la page reste accessible à qui a le lien."""
    settings = get_settings(
        {"APP_ENV": "test", "GENERATION_PROVIDER": "extractive", "OPEN_BROWSER": "false"}
    )
    response = create_app(settings_override=settings).test_client().get("/")
    assert response.status_code == 200
    assert "text/html" in response.content_type


def test_internal_page_injects_the_request_origin() -> None:
    """Le widget doit viser l'origine de la requête, jamais une URL figée."""
    settings = get_settings(
        {"APP_ENV": "test", "GENERATION_PROVIDER": "extractive", "OPEN_BROWSER": "false"}
    )
    response = create_app(settings_override=settings).test_client().get(
        "/", base_url="https://assistant.example"
    )
    corps = response.get_data(as_text=True)
    assert 'data-api-url="https://assistant.example"' in corps
    assert 'src="https://assistant.example/bcm-chat-widget.js"' in corps
    assert "__API_BASE_URL__" not in corps
    assert "__WIDGET_SCRIPT_URL__" not in corps


def test_internal_page_requires_credentials_once_configured() -> None:
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "OPEN_BROWSER": "false",
            "INTERNAL_ACCESS_USERNAME": "bcm",
            "INTERNAL_ACCESS_PASSWORD": "secret-de-test",
        }
    )
    client = create_app(settings_override=settings).test_client()

    sans_auth = client.get("/")
    assert sans_auth.status_code == 401
    assert sans_auth.headers["WWW-Authenticate"].startswith("Basic")

    def entete(utilisateur: str, mot_de_passe: str) -> dict[str, str]:
        jeton = base64.b64encode(f"{utilisateur}:{mot_de_passe}".encode()).decode()
        return {"Authorization": f"Basic {jeton}"}

    mauvais = client.get("/", headers=entete("bcm", "faux-mot-de-passe"))
    assert mauvais.status_code == 401

    bon = client.get("/", headers=entete("bcm", "secret-de-test"))
    assert bon.status_code == 200


def test_widget_script_is_served_with_the_right_content_type() -> None:
    settings = get_settings(
        {"APP_ENV": "test", "GENERATION_PROVIDER": "extractive", "OPEN_BROWSER": "false"}
    )
    response = create_app(settings_override=settings).test_client().get(
        "/bcm-chat-widget.js"
    )
    assert response.status_code == 200
    assert "javascript" in response.content_type
    assert "BCM_CHAT_CONFIG" in response.get_data(as_text=True)


def test_widget_script_is_not_gated_by_internal_access_credentials() -> None:
    """Le script n'est pas un secret : seule la page d'accueil est protégée."""
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "OPEN_BROWSER": "false",
            "INTERNAL_ACCESS_USERNAME": "bcm",
            "INTERNAL_ACCESS_PASSWORD": "secret-de-test",
        }
    )
    response = create_app(settings_override=settings).test_client().get(
        "/bcm-chat-widget.js"
    )
    assert response.status_code == 200
