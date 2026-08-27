from api.app import (
    _contextualize_followup,
    _evidence_labels,
    _explicit_report_reference_pages,
    _history_pages,
    create_app,
)
from core.config import get_settings
from tests.conftest import generation_diffusee


def test_health() -> None:
    client = create_app().test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json["pdf_pages"] == 127
    assert response.json["chart_analysis_enabled"] is False


def test_chart_question_uses_only_local_chart_analysis(monkeypatch) -> None:
    monkeypatch.setattr("api.app.select_chart_pages", lambda *args, **kwargs: [42])
    monkeypatch.setattr(
        "api.app.render_chart_pages",
        lambda *args, **kwargs: [{"pdf_page": 42, "path": "/tmp/page-42.png"}],
    )
    monkeypatch.setattr(
        "api.app.extract_chart_contexts",
        lambda *args, **kwargs: [
            {
                "pdf_page": 42,
                "text": "OCR local du graphique",
                "keyword_overlap": 5,
            }
        ],
    )
    monkeypatch.setattr(
        "api.app.explain_chart_locally",
        lambda *args, **kwargs: "Explication graphique vérifiée [p. PDF 42].",
    )
    monkeypatch.setattr(
        "api.app.stream_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("L'OCR local ne doit pas être envoyé au générateur externe.")
        ),
    )
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "SEMANTIC_RETRIEVAL": "false",
            "CHART_ANALYSIS_ENABLED": "true",
            "OPEN_BROWSER": "false",
        }
    )
    response = create_app(settings_override=settings).test_client().post(
        "/api/ask",
        json={"question": "Explique le volume des virements par mois en 2025."},
    )
    assert response.status_code == 200
    assert response.json["chart_analysis"] is True
    assert response.json["chart_pages"] == [42]
    assert response.json["answer"] == "Explication graphique vérifiée [p. PDF 42]."


def test_public_metadata_does_not_expose_generation_vendor() -> None:
    response = create_app().test_client().get("/")
    assert "openai" not in response.get_data(as_text=True).casefold()


def test_section_followup_resolves_the_title_and_priority_page() -> None:
    answer = (
        "Oui. Le rapport comporte une section intitulée "
        "« Rapport de l’Auditeur Externe » [p. PDF 119]. "
        "Il mentionne aussi un Auditeur externe dans son organisation [p. PDF 98]."
    )
    history = [
        {"role": "user", "content": "Y a-t-il un rapport d'auditeur externe ?"},
        {"role": "assistant", "content": answer},
    ]
    resolved = _contextualize_followup(
        "C'est quoi le résumé de cette section ?", history
    )
    assert "Rapport de l’Auditeur Externe" in resolved
    assert "119" in resolved
    assert _history_pages(answer, "résume cette section") == [119]


def test_exact_repeat_can_target_an_older_topic() -> None:
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    history = [
        {"role": "user", "content": "Que dit le rapport sur la liquidité bancaire ?"},
        {
            "role": "assistant",
            "content": (
                "La liquidité bancaire a progressé en 2025 [p. PDF 25]."
                "\n\n---\n**Sources consultées**\n- **Page PDF 25**"
            ),
        },
        {"role": "user", "content": "Explique l'organigramme."},
        {
            "role": "assistant",
            "content": "Le Gouverneur est au centre de l'organigramme [p. PDF 80].",
        },
    ]
    response = create_app(settings_override=settings).test_client().post(
        "/api/ask",
        json={
            "question": "Répète ce que tu disais sur la liquidité bancaire.",
            "history": history,
        },
    )
    assert response.status_code == 200
    assert response.json["memory_used"] is True
    assert response.json["clarification_needed"] is False
    assert response.json["answer"] == (
        "La liquidité bancaire a progressé en 2025 [p. PDF 25]."
    )


def test_scanned_section_followup_injects_document_ocr(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "api.app.render_chart_pages",
        lambda *args, **kwargs: [{"pdf_page": 119, "path": "/tmp/page-119.png"}],
    )
    monkeypatch.setattr(
        "api.app.extract_document_page_contexts",
        lambda *args, **kwargs: [
            {
                "chunk_id": -20119,
                "pdf_page": 119,
                "text": (
                    "[OCR documentaire local de la page PDF 119]\n"
                    "L'auditeur exprime une opinion sans réserve sur les états financiers."
                ),
                "score": 1.0,
                "lexical_score": 1.0,
                "semantic_score": None,
                "retrieval_mode": "document_ocr_local",
                "kind": "document_ocr",
            }
        ],
    )

    def fake_answer(provider, question, results, history, settings, language=None):
        captured["question"] = question
        captured["results"] = results
        return "L’auditeur exprime une opinion sans réserve [p. PDF 119]."

    monkeypatch.setattr("api.app.stream_answer", generation_diffusee(fake_answer))
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "SEMANTIC_RETRIEVAL": "false",
            "CHART_ANALYSIS_ENABLED": "true",
            "OPEN_BROWSER": "false",
        }
    )
    response = create_app(settings_override=settings).test_client().post(
        "/api/ask",
        json={
            "question": "C'est quoi le résumé de cette section ?",
            "history": [
                {
                    "role": "user",
                    "content": "Y a-t-il un rapport d'auditeur externe dans le rapport ?",
                },
                {
                    "role": "assistant",
                    "content": (
                        "Oui. Il comporte une section intitulée "
                        "« Rapport de l’Auditeur Externe » [p. PDF 119]."
                    ),
                },
            ],
        },
    )
    assert response.status_code == 200
    assert response.json["memory_used"] is True
    assert response.json["clarification_needed"] is False
    assert "Rapport de l’Auditeur Externe" in str(captured["question"])
    assert any(
        item.get("retrieval_mode") == "document_ocr_local"
        for item in captured["results"]
    )


def test_empty_question_is_invalid() -> None:
    client = create_app().test_client()
    response = client.post("/api/ask", json={"question": "  "})
    assert response.status_code == 400
    assert response.json["request_id"] == response.headers["X-Request-ID"]


def test_invalid_explicit_language_is_rejected() -> None:
    client = create_app().test_client()
    response = client.post(
        "/api/ask", json={"question": "Explique le rapport", "language": "auto"}
    )
    assert response.status_code == 400
    assert "fr" in response.json["error"]
    assert "ar" in response.json["error"]


def test_invalid_json_has_a_stable_error_contract() -> None:
    client = create_app().test_client()
    response = client.post(
        "/api/ask", data="{not-json", content_type="application/json"
    )
    assert response.status_code == 400
    assert set(response.json) == {"error", "request_id"}


def test_unknown_route_is_json_and_traceable() -> None:
    client = create_app().test_client()
    response = client.get("/missing", headers={"X-Request-ID": "test-404"})
    assert response.status_code == 404
    assert response.json["request_id"] == "test-404"
    assert response.headers["X-Request-ID"] == "test-404"


def test_question_length_is_limited() -> None:
    client = create_app().test_client()
    response = client.post("/api/ask", json={"question": "x" * 2001})
    assert response.status_code == 400


def test_reindex_is_disabled_without_token_in_production() -> None:
    settings = get_settings(
        {
            "APP_ENV": "production",
            "GENERATION_PROVIDER": "extractive",
            "REINDEX_TOKEN": "",
        }
    )
    response = create_app(settings_override=settings).test_client().post("/api/reindex")
    assert response.status_code == 403
    assert response.json["error"] == "Réindexation non autorisée."


def test_cors_header_is_absent_by_default() -> None:
    client = create_app().test_client()
    response = client.get("/health", headers={"Origin": "https://bcm.mr"})
    assert "Access-Control-Allow-Origin" not in response.headers


def test_cors_header_allows_only_configured_origins() -> None:
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "CORS_ALLOWED_ORIGINS": "https://bcm.mr",
        }
    )
    client = create_app(settings_override=settings).test_client()

    allowed = client.get("/api/ask", headers={"Origin": "https://bcm.mr"})
    assert allowed.headers.get("Access-Control-Allow-Origin") == "https://bcm.mr"

    blocked = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in blocked.headers


def test_ask_is_rate_limited_per_client() -> None:
    settings = get_settings(
        {
            "APP_ENV": "development",
            "GENERATION_PROVIDER": "extractive",
            "SEMANTIC_RETRIEVAL": "false",
            "CHART_ANALYSIS_ENABLED": "false",
            "OPEN_BROWSER": "false",
            "RATE_LIMIT_ASK": "2 per minute",
        }
    )
    client = create_app(settings_override=settings).test_client()
    payload = {"question": "Quel a été le taux de croissance du PIB réel en 2025 ?"}

    first = client.post("/api/ask", json=payload)
    second = client.post("/api/ask", json=payload)
    third = client.post("/api/ask", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json["error"]
    assert third.json["request_id"] == third.headers["X-Request-ID"]


def test_unrelated_question_refuses() -> None:
    client = create_app().test_client()
    response = client.post(
        "/api/ask", json={"question": "Quelle est la capitale du Japon ?"}
    )
    assert response.status_code == 200
    assert response.json["grounded"] is False


def test_precise_growth_answer() -> None:
    client = create_app().test_client()
    response = client.post(
        "/api/ask",
        json={"question": "Quel a été le taux de croissance du PIB réel en 2025 ?"},
    )
    assert response.status_code == 200
    assert "4,0%" in response.json["answer"].replace(" ", "")
    assert any(
        citation in response.json["answer"]
        for citation in ("p. PDF 5", "p. PDF 21")
    )
    assert len(response.json["answer"]) < 900


def test_growth_paraphrase_does_not_confuse_total_and_non_extractive_activity() -> None:
    client = create_app().test_client()
    response = client.post(
        "/api/ask",
        json={
            "question": "De combien l'activité économique mauritanienne "
            "a-t-elle progressé en volume durant l'exercice ?"
        },
    )
    answer = response.json["answer"].replace(" ", "")
    assert "4,0%" in answer
    assert response.json["grounded"] is True


def test_ambiguous_question_requests_confirmation(
    monkeypatch,
) -> None:
    planned_queries = [
        "Comparer les dépôts de la clientèle du secteur bancaire entre 2024 et 2025",
        "Comparer les dépôts des banques auprès de la BCM entre 2024 et 2025",
    ]
    monkeypatch.setattr(
        "api.app.plan_queries_openai",
        lambda question, settings: {
            "queries": [question, *planned_queries],
            "ambiguous": True,
            "suggestions": [
                "Comparer des seuils minimaux absents du rapport",
                "Comparer un autre indicateur non retrouvé",
            ],
        },
    )
    monkeypatch.setattr(
        "api.app.rerank_openai",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Le reranker ne doit pas choisir avant clarification.")
        ),
    )
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "openai",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    client = create_app(settings_override=settings).test_client()
    response = client.post(
        "/api/ask",
        json={
            "question": "Le montant de dépôt dans les banques, comparaison entre 2024 et 2025"
        },
    )
    assert response.status_code == 200
    assert response.json["grounded"] is False
    assert response.json["clarification_needed"] is True
    assert len(response.json["suggestions"]) >= 2
    assert any("Dépôts de la clientèle" in value for value in response.json["suggestions"])
    assert any("Banques et établissements financiers" in value for value in response.json["suggestions"])
    assert all("absent" not in value for value in response.json["suggestions"])
    assert "134,0" not in response.json["answer"]
    assert all(value in response.json["answer"] for value in response.json["suggestions"])


def test_confirmed_table_scope_is_answered_without_a_stored_fact() -> None:
    client = create_app().test_client()
    response = client.post(
        "/api/ask",
        json={
            "question": "Comparer les dépôts de la clientèle entre 2024 et 2025"
        },
    )
    answer = response.json["answer"].replace(" ", "")
    assert response.status_code == 200
    assert response.json["grounded"] is True
    assert "134,0" in answer
    assert "158,7" in answer


def test_printed_table_page_is_resolved_to_the_actual_pdf_page() -> None:
    app = create_app()
    engine = app.extensions["bcm_engine"]
    engine.load()
    assert _explicit_report_reference_pages(
        "dans la page 59 tableau 5", engine.chunks
    ) == [30]


def test_monetary_aggregates_do_not_trigger_a_false_clarification(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "api.app.plan_queries_openai",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Le titre exact du rapport ne doit pas être déclaré ambigu.")
        ),
    )
    monkeypatch.setattr(
        "api.app.rerank_openai", lambda question, results, settings: results[:8]
    )

    def fake_answer(provider, question, results, history, settings, language=None):
        assert any(int(item["pdf_page"]) == 30 for item in results)
        assert any("Monnaie au sens large" in item["text"] for item in results)
        return (
            "Les agrégats monétaires sont présentés dans le tableau 5 "
            "[p. PDF 30]."
        )

    monkeypatch.setattr("api.app.stream_answer", generation_diffusee(fake_answer))
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "openai",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    client = create_app(settings_override=settings).test_client()
    first = client.post(
        "/api/ask", json={"question": "c'est quoi les agrégats monétaires"}
    )
    assert first.status_code == 200
    assert first.json["clarification_needed"] is False
    assert any(source["pdf_page"] == 30 for source in first.json["sources"])

    second = client.post(
        "/api/ask",
        json={
            "question": "dans la page 59 tableau 5",
            "history": [
                {
                    "role": "user",
                    "content": "c'est quoi les agrégats monétaires",
                },
                {
                    "role": "assistant",
                    "content": first.json["answer"],
                },
            ],
        },
    )
    assert second.status_code == 200
    assert second.json["clarification_needed"] is False
    assert second.json["memory_used"] is True
    assert second.json["sources"][0]["pdf_page"] == 30


def test_liquidity_comparison_is_answered_without_false_clarification() -> None:
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    client = create_app(settings_override=settings).test_client()
    response = client.post(
        "/api/ask",
        json={"question": "C'est quoi la liquidité bancaire en 2025 à 2024 ?"},
    )
    answer = response.json["answer"].replace(" ", "")
    assert response.status_code == 200
    assert response.json["grounded"] is True
    assert response.json["clarification_needed"] is False
    assert "214%" in answer
    assert "212%" in answer


def test_generic_report_headers_are_not_evidence_labels() -> None:
    labels = _evidence_labels(
        "Rapport annuel 2025 Rapport annuel 2025\n"
        "Les actifs, s’est établie à 218,5 milliards en 2025, en hausse de 17,7%."
    )
    assert all("rapport annuel" not in label.casefold() for label in labels)
    assert all("s’est établie" not in label.casefold() for label in labels)


def test_confirmed_indicator_does_not_trigger_a_second_clarification(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "api.app.plan_queries_openai",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Un indicateur déjà confirmé ne doit pas être replanifié.")
        ),
    )
    monkeypatch.setattr(
        "api.app.rerank_openai",
        lambda question, results, settings: results[:4],
    )
    monkeypatch.setattr(
        "api.app.stream_answer",
        generation_diffusee(
            lambda *args, **kwargs: "Indicateur confirmé et réponse produite [p. PDF 33]."
        ),
    )
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "openai",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    client = create_app(settings_override=settings).test_client()
    response = client.post(
        "/api/ask",
        json={
            "question": "Comparer l'indicateur « Coefficient de liquidité moyen — "
            "secteur bancaire » entre 2024 et 2025"
        },
    )
    assert response.status_code == 200
    assert response.json["clarification_needed"] is False
    assert response.json["suggestions"] == []
    # Vérifie que la génération simulée a bien été atteinte : sans cette
    # assertion, un repli silencieux ferait passer le test sans rien prouver.
    assert "Indicateur confirmé" in response.json["answer"]


def test_precise_reserves_answer() -> None:
    client = create_app().test_client()
    response = client.post(
        "/api/ask",
        json={"question": "Quel était le niveau des réserves officielles brutes fin 2025 ?"},
    )
    answer = response.json["answer"].replace(" ", "")
    assert "2,2milliardsdedollars" in answer
    assert "5,9mois" in answer


def test_health_does_not_expose_internal_fingerprints() -> None:
    """Les empreintes du corpus n'ont pas à figurer sur un point d'entrée public."""
    payload = create_app().test_client().get("/health").json
    assert "corpus_fingerprint" not in payload
    assert "report_sha256" not in payload
    assert "manifest" not in payload
    # Ce qui sert réellement à surveiller le service reste publié.
    assert payload["chunks"] > 2000
    assert payload["documents"] >= 1
    assert payload["semantic_index"] in (True, False)
