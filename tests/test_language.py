"""Tests du choix automatique entre le français et l'arabe."""

import sys
from types import SimpleNamespace

from api.app import _clarification_answer, create_app
from api.providers import generate_openai
from core.config import get_settings
from core.language import (
    answer_language_instruction,
    format_arabic_bidi,
    is_arabic_text,
    LTR_ISOLATE,
    missing_information_message,
    normalize_arabic_units,
    response_language,
    POP_DIRECTIONAL_ISOLATE,
    untranslated_latin_words,
)


def test_arabic_language_detection_and_fixed_messages() -> None:
    assert is_arabic_text("ما هو معدل التضخم في عام 2025؟") is True
    assert response_language("ما هو معدل التضخم؟") == "ar"
    assert response_language("Quel est le taux d'inflation ?") == "fr"
    assert "العربية" in answer_language_instruction("اشرح التضخم")
    assert "العربية" in answer_language_instruction(
        "Explique les systèmes de paiement", "ar"
    )
    assert "français" in answer_language_instruction("اشرح التضخم", "fr")
    assert "لا أجد" in missing_information_message("ar")


def test_explicit_api_language_overrides_question_detection(monkeypatch) -> None:
    """Le choix Gradio est prioritaire même si la question est dans l'autre langue."""
    captured: dict[str, str] = {}

    def fake_answer(
        provider, question, results, history, settings, language=None
    ):
        captured["language"] = language
        assert not is_arabic_text(question)
        return "بلغ معدل النمو الحقيقي 4.0٪ [p. PDF 5]."

    monkeypatch.setattr("api.app.answer_with_provider", fake_answer)
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "openai",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    response = create_app(settings_override=settings).test_client().post(
        "/api/ask",
        json={
            "question": "Quel a été le taux de croissance du PIB réel en 2025 ?",
            "language": "ar",
        },
    )
    assert response.status_code == 200
    assert captured["language"] == "ar"
    assert response.json["language"] == "ar"
    assert response.json["answer"].startswith("بلغ")


def test_french_units_are_normalized_and_mixed_words_are_detected() -> None:
    mixed = "بلغت القيمة 2,4 milliards de MRU [p. PDF 45]."
    normalized = normalize_arabic_units(mixed)
    assert "milliards" not in normalized
    assert "مليار أوقية موريتانية" in normalized
    assert untranslated_latin_words(normalized) == []
    assert untranslated_latin_words("هذه valeur غير مترجمة") == ["valeur"]


def test_arabic_bidi_keeps_years_amounts_percentages_and_citations_in_place() -> None:
    answer = (
        "ارتفعت الودائع بين 2023 و2025 من 134,0 مليار mRU إلى "
        "158,7 مليار MRU، وبلغ الحجم 300 000 عملية، وانخفض المعامل "
        "من 90% إلى 77% [p. PDF 33]."
    )
    formatted = format_arabic_bidi(answer)
    isolate = lambda value: f"{LTR_ISOLATE}{value}{POP_DIRECTIONAL_ISOLATE}"

    assert isolate("2023") in formatted
    assert isolate("2025") in formatted
    assert isolate("134,0") in formatted
    assert isolate("300\u202f000") in formatted
    assert f"{isolate('300')} {isolate('000')}" not in formatted
    assert isolate("90٪") in formatted
    assert isolate("77٪") in formatted
    assert isolate("[p. PDF 33]") in formatted
    assert "MRU" not in formatted.upper()
    assert "أوقية موريتانية" in formatted


def test_official_payment_acronyms_are_allowed_in_an_arabic_answer() -> None:
    """Les noms techniques officiels ne doivent pas invalider toute la réponse."""
    answer = (
        "يشمل الإصلاح اعتماد معيار ISO 20022 وربط نظام RTGS مع "
        "SWIFT وGIMTEL [p. PDF 65]."
    )
    assert untranslated_latin_words(answer) == []


def test_openai_generation_keeps_a_valid_arabic_answer_with_payment_standards(
    monkeypatch,
) -> None:
    """Couvre directement l'ancienne ValueError du validateur arabe."""
    calls: list[dict] = []

    def create_response(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            output_text=(
                "يشمل الإصلاح اعتماد معيار ISO 20022 وربط نظام RTGS مع "
                "SWIFT وGIMTEL [p. PDF 65]."
            )
        )

    fake_client = SimpleNamespace(
        responses=SimpleNamespace(create=create_response)
    )
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda: fake_client),
    )
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "openai",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    answer = generate_openai(
        "ما هي الإصلاحات التي يجري طرحها لأنظمة الدفع؟",
        [
            {
                "chunk_id": 1,
                "pdf_page": 65,
                "text": "Application de la norme ISO 20022.",
                "score": 0.5,
            }
        ],
        [],
        settings,
    )
    assert len(calls) == 1
    assert calls[0]["reasoning"] == {"effort": "low"}
    assert calls[0]["max_output_tokens"] == 1000
    assert "ISO 20022" in answer
    assert "تعذر إنشاء الإجابة" not in answer


def test_arabic_clarification_is_not_returned_in_french() -> None:
    answer = _clarification_answer(
        ["الخيار الأول", "الخيار الثاني"], language="ar"
    )
    assert "يرجى تأكيد" in answer
    assert "Pouvez-vous" not in answer


def test_arabic_question_uses_french_retrieval_and_arabic_generation(
    monkeypatch,
) -> None:
    def forbidden_plan(*args, **kwargs):
        raise AssertionError("Le glossaire bilingue suffit pour cette question.")

    monkeypatch.setattr("api.app.plan_queries_openai", forbidden_plan)
    monkeypatch.setattr(
        "api.app.rerank_openai", lambda question, results, settings: results[:5]
    )

    def fake_answer(provider, question, results, history, settings, language=None):
        assert is_arabic_text(question)
        assert any("4,0%" in item["text"].replace(" ", "") for item in results)
        return "بلغ معدل نمو الناتج المحلي الإجمالي الحقيقي 4.0٪ [p. PDF 5]."

    monkeypatch.setattr("api.app.answer_with_provider", fake_answer)
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "openai",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    response = create_app(settings_override=settings).test_client().post(
        "/api/ask",
        json={
            "question": "ما هو معدل نمو الناتج المحلي الإجمالي الحقيقي في عام 2025؟"
        },
    )
    assert response.status_code == 200
    assert response.json["language"] == "ar"
    assert "بلغ معدل" in response.json["answer"]
    assert response.json["grounded"] is True


def test_arabic_payment_reforms_use_the_fast_path_and_both_relevant_pages(
    monkeypatch,
) -> None:
    """La question signalée doit éviter planification/reranking sans perdre les preuves."""
    def forbidden_call(*args, **kwargs):
        raise AssertionError("Le chemin arabe rapide ne doit pas appeler ce service.")

    generated: list[list[int]] = []

    def fake_answer(provider, question, results, history, settings, language=None):
        generated.append([int(item["pdf_page"]) for item in results])
        assert is_arabic_text(question)
        assert 64 in generated[-1]
        assert 65 in generated[-1]
        return (
            "تشمل الإصلاحات تعزيز الإشراف على أنظمة الدفع ووضع خرائط للمخاطر "
            "[p. PDF 64]، واعتماد معيار ISO 20022 وإنشاء قاعدة مركزية لحوادث "
            "الدفع [p. PDF 65]."
        )

    monkeypatch.setattr("api.app.plan_queries_openai", forbidden_call)
    monkeypatch.setattr("api.app.rerank_openai", forbidden_call)
    monkeypatch.setattr("api.app.answer_with_provider", fake_answer)
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "openai",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    response = create_app(settings_override=settings).test_client().post(
        "/api/ask",
        json={"question": "ما هي الإصلاحات التي يجري طرحها لأنظمة الدفع؟"},
    )
    assert response.status_code == 200
    assert len(generated) == 1
    assert response.json["language"] == "ar"
    assert "تعذر إنشاء الإجابة" not in response.json["answer"]
    assert "ISO 20022" in response.json["answer"]
    assert response.json["grounded"] is True


def test_arabic_followup_reuses_a_page_from_a_french_turn(monkeypatch) -> None:
    def fake_answer(provider, question, results, history, settings, language=None):
        assert is_arabic_text(question)
        assert any(int(item["pdf_page"]) == 25 for item in results)
        return "شهدت السيولة المصرفية تحسناً خلال سنة 2025 [p. PDF 25]."

    monkeypatch.setattr("api.app.answer_with_provider", fake_answer)
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
        }
    )
    response = create_app(settings_override=settings).test_client().post(
        "/api/ask",
        json={
            "question": "لخص هذا الموضوع بالعربية",
            "history": [
                {
                    "role": "user",
                    "content": "Que dit le rapport sur la liquidité bancaire ?",
                },
                {
                    "role": "assistant",
                    "content": "La liquidité bancaire progresse en 2025 [p. PDF 25].",
                },
            ],
        },
    )
    assert response.status_code == 200
    assert response.json["language"] == "ar"
    assert response.json["memory_used"] is True
    assert "السيولة المصرفية" in response.json["answer"]
