"""Lecture visuelle des graphiques par Gemini : client simulé, aucun appel réseau."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from api.app import create_app
from google.genai import errors as genai_errors

from api.charts import (
    extract_chart_contexts_gemini,
    format_chart_vision_context,
    parse_chart_vision_json,
    select_relevant_charts,
)
from api.usage import begin_usage_collection, reset_usage_collection, usage_snapshot
from core.config import ConfigurationError, get_settings
from tests.conftest import generation_diffusee


VISION_JSON = {
    "graphiques": [
        {
            "numero": "Graphique 12",
            "titre": "Évolution des dépôts et des crédits",
            "type": "barres",
            "axes": {
                "abscisse": {"libelle": "Années", "unite": None},
                "ordonnee": {"libelle": "Encours", "unite": "milliards de MRU"},
                "ordonnee_secondaire": None,
            },
            "unite": "milliards de MRU",
            "periode": "2023-2025",
            "source": "BCM",
            "series": [
                {
                    "nom": "Dépôts",
                    "unite": None,
                    "valeurs": [
                        {"etiquette": "2023", "valeur": "120,4", "lisible": True},
                        {"etiquette": "2024", "valeur": None, "lisible": False},
                        {"etiquette": "2025", "valeur": "141,9", "lisible": True},
                        {
                            "etiquette": "2026",
                            "valeur": "150",
                            "lisible": True,
                            "approximatif": True,
                        },
                    ],
                }
            ],
            "tendance_principale": "Les dépôts progressent sur la période.",
            "elements_illisibles": ["valeur des dépôts en 2024"],
        }
    ]
}


class _FakeModels:
    def __init__(self, responses: dict[str, object]) -> None:
        # Réponse (texte JSON) ou exception, choisie selon le nom du PNG envoyé.
        self._responses = responses
        self.calls: list[dict] = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        image = contents[0]
        data = image.inline_data.data
        outcome = self._responses[data.decode("utf-8")]
        if isinstance(outcome, list):
            # Suite de réponses successives pour une même page (nouvelle tentative).
            outcome = outcome.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            text=outcome,
            candidates=[SimpleNamespace(finish_reason="STOP")],
            usage_metadata=SimpleNamespace(
                prompt_token_count=1500,
                candidates_token_count=400,
                thoughts_token_count=300,
                total_token_count=2200,
            ),
        )


def _install_fake_client(monkeypatch, models: _FakeModels) -> None:
    class FakeClient:
        def __init__(self, api_key: str, http_options=None) -> None:
            assert api_key == "fake-key"
            assert http_options.timeout > 0
            self.models = models

    monkeypatch.setattr("google.genai.Client", FakeClient)


def _page(tmp_path, page: int, marker: str) -> dict:
    # Le contenu du « PNG » sert de clé au client simulé : aucune image réelle
    # n'est nécessaire puisque rien n'est envoyé sur le réseau.
    path = tmp_path / f"abc-page-{page}.png"
    path.write_bytes(marker.encode("utf-8"))
    return {"pdf_page": page, "path": path}


def test_parse_accepts_code_fences_and_drops_empty_charts() -> None:
    text = "```json\n" + json.dumps(
        {"graphiques": [VISION_JSON["graphiques"][0], {"series": "invalide"}, "x"]}
    ) + "\n```"
    charts = parse_chart_vision_json(text)
    assert len(charts) == 1
    assert charts[0]["numero"] == "Graphique 12"
    assert parse_chart_vision_json('{"graphiques": []}') == []


@pytest.mark.parametrize("text", ["", "pas de JSON", '{"autre": 1}', '"chaîne"'])
def test_parse_rejects_unusable_output(text: str) -> None:
    with pytest.raises(ValueError):
        parse_chart_vision_json(text)


def test_format_marks_unreadable_values_without_inventing_them() -> None:
    charts = parse_chart_vision_json(json.dumps(VISION_JSON))
    text = format_chart_vision_context(42, charts)
    assert text.splitlines()[0] == "[Lecture visuelle de la page PDF 42]"
    assert "Graphique 12 : Évolution des dépôts et des crédits" in text
    assert "ordonnée = Encours (milliards de MRU)" in text
    assert (
        "2023 = 120,4; 2024 = illisible; 2025 = 141,9; "
        "2026 ≈ 150 (estimation visuelle)"
    ) in text
    assert "Éléments illisibles (ne pas deviner) : valeur des dépôts en 2024" in text
    assert "Gemini" not in text


def test_extract_returns_contexts_records_usage_and_caches(tmp_path, monkeypatch) -> None:
    models = _FakeModels({"p42": json.dumps(VISION_JSON)})
    _install_fake_client(monkeypatch, models)
    pages = [_page(tmp_path, 42, "p42")]
    token = begin_usage_collection()
    try:
        contexts = extract_chart_contexts_gemini(
            "Explique le graphique des dépôts", pages, "fake-key", "gemini-test-flash"
        )
        usage = usage_snapshot()
    finally:
        reset_usage_collection(token)

    assert len(contexts) == 1
    context = contexts[0]
    assert context["kind"] == "chart_vision"
    assert context["retrieval_mode"] == "chart_gemini_vision"
    assert context["pdf_page"] == 42
    assert context["score"] == 1.0
    assert context["keyword_overlap"] >= 1
    assert "141,9" in context["text"]
    assert models.calls[0]["model"] == "gemini-test-flash"
    assert models.calls[0]["config"].response_mime_type == "application/json"
    # Le collecteur suit l'appel malgré l'exécution dans un fil de travail.
    assert [call["operation"] for call in usage] == ["chart_vision"]
    assert usage[0]["input_tokens"] == 1500 and usage[0]["success"] is True

    cache_files = list(tmp_path.glob("abc-page-42.*.vision.json"))
    assert len(cache_files) == 1

    # Même question, casse et ponctuation près : servie depuis le cache.
    again = extract_chart_contexts_gemini(
        "explique le GRAPHIQUE des dépôts ?", pages, "fake-key", "gemini-test-flash"
    )
    assert again[0]["text"] == context["text"]
    assert len(models.calls) == 1

    # Une autre question ou un autre modèle relance une lecture.
    extract_chart_contexts_gemini(
        "Que montre le graphique des crédits ?", pages, "fake-key", "gemini-test-flash"
    )
    assert len(models.calls) == 2


def test_extract_raises_and_caches_nothing_when_every_page_fails(
    tmp_path, monkeypatch
) -> None:
    models = _FakeModels({"p42": RuntimeError("quota"), "p43": "réponse non JSON"})
    _install_fake_client(monkeypatch, models)
    pages = [_page(tmp_path, 42, "p42"), _page(tmp_path, 43, "p43")]
    token = begin_usage_collection()
    try:
        with pytest.raises((RuntimeError, ValueError)):
            extract_chart_contexts_gemini("Explique le graphique", pages, "fake-key", "m")
        usage = usage_snapshot()
    finally:
        reset_usage_collection(token)
    assert not list(tmp_path.glob("*.vision.json"))
    assert {call["success"] for call in usage} == {False, True}
    # Une erreur qui n'est pas une panne serveur n'est pas retentée.
    assert len(models.calls) == 2


def test_extract_retries_once_on_server_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("api.charts.CHART_VISION_RETRY_DELAY_S", 0)
    panne = genai_errors.ServerError(
        503, {"error": {"message": "surcharge", "status": "UNAVAILABLE"}}
    )
    models = _FakeModels({"p42": [panne, json.dumps(VISION_JSON)]})
    _install_fake_client(monkeypatch, models)
    token = begin_usage_collection()
    try:
        contexts = extract_chart_contexts_gemini(
            "Explique le graphique des dépôts",
            [_page(tmp_path, 42, "p42")],
            "fake-key",
            "m",
        )
        usage = usage_snapshot()
    finally:
        reset_usage_collection(token)
    assert [context["pdf_page"] for context in contexts] == [42]
    assert len(models.calls) == 2
    assert [call["success"] for call in usage] == [False, True]


def test_extract_gives_up_after_two_server_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("api.charts.CHART_VISION_RETRY_DELAY_S", 0)
    panne = genai_errors.ServerError(
        500, {"error": {"message": "interne", "status": "INTERNAL"}}
    )
    models = _FakeModels({"p42": [panne, panne]})
    _install_fake_client(monkeypatch, models)
    with pytest.raises(genai_errors.ServerError):
        extract_chart_contexts_gemini(
            "Explique le graphique", [_page(tmp_path, 42, "p42")], "fake-key", "m"
        )
    assert len(models.calls) == 2


def _chart(numero: str, titre: str, axe: str = "", series: tuple[str, ...] = ()) -> dict:
    return {
        "numero": numero,
        "titre": titre,
        "axes": {"ordonnee": {"libelle": axe, "unite": None}},
        "series": [{"nom": nom, "valeurs": []} for nom in series],
    }


def test_relevance_keeps_the_requested_chart_number() -> None:
    charts = [
        _chart("Graphique 22", "Taux des opérations de politique monétaire"),
        _chart("Graphique 23", "Évolution de la liquidité bancaire depuis 2022"),
    ]
    kept = select_relevant_charts("Explique le graphique 23 sur la liquidité.", charts)
    assert [chart["numero"] for chart in kept] == ["Graphique 23"]


def test_relevance_drops_neighbouring_charts_sharing_one_or_two_words() -> None:
    # Cas réel : la question vise dépôts, crédits et intermédiation ; la page
    # retenue ne porte que des graphiques sur la transformation par banque.
    charts = [
        _chart(
            "Graphique 46",
            "Taux de transformation par banque avant et après retrait de 100% "
            "des dépôts publics et semi-publics",
            series=("Banques avec FPN impactés",),
        ),
        _chart("Graphique 47", "Crédits Bruts et Fonds propres nets par banque"),
    ]
    question = (
        "Explique le graphique sur l'évolution des dépôts, des crédits "
        "et du taux d'intermédiation."
    )
    assert select_relevant_charts(question, charts) == []


def test_relevance_prefers_the_closest_twin_chart() -> None:
    charts = [
        _chart("Graphique 74", "RTGS, total paiements client SCCT par jour", "Volume"),
        _chart("Graphique 76", "ACH, Virements volume et valeur par mois en 2025"),
        _chart(
            "Graphique 77",
            "ACH, Chèques volume et valeur par mois en 2025",
            "Volume Transferts",
        ),
    ]
    kept = select_relevant_charts(
        "Explique le volume des virements par mois en 2025.", charts
    )
    assert [chart["numero"] for chart in kept] == ["Graphique 76"]


def test_extract_returns_nothing_when_no_chart_matches(tmp_path, monkeypatch) -> None:
    models = _FakeModels({"p42": json.dumps(VISION_JSON)})
    _install_fake_client(monkeypatch, models)
    contexts = extract_chart_contexts_gemini(
        "Explique le graphique sur l'inflation et les prix alimentaires",
        [_page(tmp_path, 42, "p42")],
        "fake-key",
        "m",
    )
    assert contexts == []
    # La lecture de la page est mise en cache même si rien ne correspond.
    assert len(list(tmp_path.glob("*.vision.json"))) == 1


def test_extract_keeps_readable_pages_when_another_fails(tmp_path, monkeypatch) -> None:
    models = _FakeModels(
        {"p42": json.dumps(VISION_JSON), "p43": RuntimeError("délai dépassé")}
    )
    _install_fake_client(monkeypatch, models)
    pages = [_page(tmp_path, 42, "p42"), _page(tmp_path, 43, "p43")]
    contexts = extract_chart_contexts_gemini(
        "Explique le graphique", pages, "fake-key", "m"
    )
    assert [context["pdf_page"] for context in contexts] == [42]


def test_extract_requires_an_api_key(tmp_path) -> None:
    with pytest.raises(RuntimeError):
        extract_chart_contexts_gemini(
            "Explique le graphique", [_page(tmp_path, 42, "p42")], "", "m"
        )


def test_backend_settings_defaults_and_validation() -> None:
    settings = get_settings({"APP_ENV": "development"})
    assert settings.chart_analysis_backend == "local_ocr"
    assert settings.chart_vision_model == "gemini-3.6-flash"

    vision = get_settings(
        {"APP_ENV": "test", "CHART_ANALYSIS_BACKEND": "GEMINI_VISION"}
    )
    assert vision.chart_analysis_backend == "gemini_vision"

    with pytest.raises(ConfigurationError):
        get_settings({"APP_ENV": "test", "CHART_ANALYSIS_BACKEND": "tesseract"})

    production = {
        "APP_ENV": "production",
        "GENERATION_PROVIDER": "extractive",
        "CHART_ANALYSIS_ENABLED": "true",
        "CHART_ANALYSIS_BACKEND": "gemini_vision",
    }
    with pytest.raises(ConfigurationError):
        get_settings(production)
    assert get_settings({**production, "GEMINI_API_KEY": "k"}).chart_analysis_enabled
    # Analyse désactivée : la clé n'est pas exigée.
    get_settings({**production, "CHART_ANALYSIS_ENABLED": "false"})


def _chart_settings(backend: str):
    return get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "SEMANTIC_RETRIEVAL": "false",
            "CHART_ANALYSIS_ENABLED": "true",
            "CHART_ANALYSIS_BACKEND": backend,
            "GEMINI_API_KEY": "fake-key",
            "OPEN_BROWSER": "false",
        }
    )


def _patch_chart_pipeline(monkeypatch) -> None:
    monkeypatch.setattr("api.app.select_chart_pages", lambda *args, **kwargs: [42])
    monkeypatch.setattr(
        "api.app.render_chart_pages",
        lambda *args, **kwargs: [{"pdf_page": 42, "path": "/tmp/page-42.png"}],
    )
    monkeypatch.setattr(
        "api.app.extract_chart_contexts",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("L'OCR local ne doit pas tourner avec gemini_vision.")
        ),
    )
    monkeypatch.setattr(
        "api.app.explain_chart_locally",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Les règles locales ne lisent pas la sortie vision.")
        ),
    )


def test_gemini_backend_feeds_vision_contexts_to_generator(monkeypatch) -> None:
    _patch_chart_pipeline(monkeypatch)
    vision_text = format_chart_vision_context(
        42, parse_chart_vision_json(json.dumps(VISION_JSON))
    )
    monkeypatch.setattr(
        "api.app.extract_chart_contexts_gemini",
        lambda *args, **kwargs: [
            {
                "chunk_id": -30042,
                "pdf_page": 42,
                "text": vision_text,
                "score": 1.0,
                "lexical_score": 1.0,
                "semantic_score": None,
                "retrieval_mode": "chart_gemini_vision",
                "kind": "chart_vision",
                "keyword_overlap": 3,
                "query_keyword_count": 5,
            }
        ],
    )
    captured: dict[str, object] = {}

    def fake_answer(provider, question, results, history, settings, language=None):
        captured["results"] = results
        return "Les dépôts atteignent 141,9 milliards de MRU en 2025 [p. PDF 42]."

    monkeypatch.setattr("api.app.stream_answer", generation_diffusee(fake_answer))
    response = create_app(settings_override=_chart_settings("gemini_vision")).test_client().post(
        "/api/ask",
        json={"question": "Explique le graphique sur l'évolution des dépôts."},
    )
    assert response.status_code == 200
    assert response.json["chart_analysis"] is True
    assert response.json["chart_pages"] == [42]
    assert response.json["grounded"] is True
    first = captured["results"][0]
    assert first["kind"] == "chart_vision"
    assert first["citation"] == "p. PDF 42"
    assert any(source["pdf_page"] == 42 for source in response.json["sources"])


def test_gemini_backend_failure_falls_back_to_documentary_answer(monkeypatch) -> None:
    _patch_chart_pipeline(monkeypatch)
    monkeypatch.setattr(
        "api.app.extract_chart_contexts_gemini",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("quota")),
    )
    captured: dict[str, object] = {}

    def fake_answer(provider, question, results, history, settings, language=None):
        captured["results"] = results
        return "Les dépôts progressent selon le texte du rapport [p. PDF 42]."

    monkeypatch.setattr("api.app.stream_answer", generation_diffusee(fake_answer))
    response = create_app(settings_override=_chart_settings("gemini_vision")).test_client().post(
        "/api/ask",
        json={"question": "Explique le graphique sur l'évolution des dépôts."},
    )
    assert response.status_code == 200
    assert response.json["chart_analysis"] is False
    assert response.json["chart_pages"] == []
    assert captured["results"]
    assert all(item.get("kind") != "chart_vision" for item in captured["results"])


def test_local_backend_never_calls_gemini(monkeypatch) -> None:
    monkeypatch.setattr("api.app.select_chart_pages", lambda *args, **kwargs: [42])
    monkeypatch.setattr(
        "api.app.render_chart_pages",
        lambda *args, **kwargs: [{"pdf_page": 42, "path": "/tmp/page-42.png"}],
    )
    monkeypatch.setattr(
        "api.app.extract_chart_contexts",
        lambda *args, **kwargs: [
            {"pdf_page": 42, "text": "OCR local du graphique", "keyword_overlap": 5}
        ],
    )
    monkeypatch.setattr(
        "api.app.explain_chart_locally",
        lambda *args, **kwargs: "Explication graphique vérifiée [p. PDF 42].",
    )
    monkeypatch.setattr(
        "api.app.extract_chart_contexts_gemini",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local_ocr ne doit jamais envoyer d'image à Gemini.")
        ),
    )
    response = create_app(settings_override=_chart_settings("local_ocr")).test_client().post(
        "/api/ask",
        json={"question": "Explique le volume des virements par mois en 2025."},
    )
    assert response.status_code == 200
    assert response.json["chart_analysis"] is True
    assert response.json["answer"] == "Explication graphique vérifiée [p. PDF 42]."
