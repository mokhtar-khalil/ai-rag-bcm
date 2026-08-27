"""Vérifie l'indexation d'un corpus mêlant le rapport PDF et les pages du site."""

from __future__ import annotations

from pathlib import Path

import pytest

from api.rag import RAGIndex
from api.sources import (
    LETTRE_UNIT_OFFSET,
    SOURCE_TYPE_LETTRE,
    SOURCE_TYPE_PDF,
    SOURCE_TYPE_WEB,
    WEB_UNIT_OFFSET,
    Document,
    Segment,
    build_registry,
    citation_label,
    load_lettres_documents,
    load_web_documents,
    ocr_text_from_items,
)


PAGES = [
    {
        "doc_id": "page-805",
        "title": "Le Gouverneur",
        "url": "https://www.bcm.mr/page/gouverneur/805",
        "text": (
            "Le Gouverneur dirige la Banque Centrale de Mauritanie et preside son "
            "Conseil National. Il est nomme par decret pour un mandat determine et "
            "represente l'institution aupres des organisations internationales."
        ),
    },
    {
        "doc_id": "page-808",
        "title": "Organigramme",
        "url": "https://www.bcm.mr/page/organigramme/808",
        "text": (
            "L'organigramme de la Banque Centrale de Mauritanie distingue les "
            "instances de gouvernance, les fonctions d'appui et les directions "
            "operationnelles chargees de la supervision bancaire."
        ),
    },
    {
        "doc_id": "page-810",
        "title": "Lois et textes fondateurs",
        "url": "https://www.bcm.mr/page/lois-et-textes-fondateurs/810",
        "text": (
            "Les lois et textes fondateurs precisent les missions monetaires de "
            "l'institution, son capital et les regles de publication de ses "
            "comptes annuels certifies."
        ),
    },
]


def web_engine(tmp_path: Path, pages: list[dict[str, str]] | None = None) -> RAGIndex:
    """Construit un index autonome sur des pages du site, sans rapport PDF."""
    engine = RAGIndex(tmp_path / "absent.pdf", tmp_path / "index.joblib")
    engine.build(load_web_documents(pages or PAGES))
    return engine


def test_web_pages_are_indexed_and_retrieved(tmp_path: Path) -> None:
    engine = web_engine(tmp_path)
    results = engine.retrieve("Qui dirige la Banque Centrale de Mauritanie ?")
    assert results
    assert "Gouverneur" in results[0]["text"]


def test_web_units_never_collide_with_pdf_pages(tmp_path: Path) -> None:
    engine = web_engine(tmp_path)
    assert all(chunk.pdf_page >= WEB_UNIT_OFFSET for chunk in engine.chunks)
    assert {chunk.doc_id for chunk in engine.chunks} == {"page-805", "page-808", "page-810"}


def test_registry_exposes_a_public_url_for_a_web_chunk(tmp_path: Path) -> None:
    engine = web_engine(tmp_path)
    entry = engine.source_registry[WEB_UNIT_OFFSET]
    assert entry["url"] == "https://www.bcm.mr/page/gouverneur/805"
    assert entry["source_type"] == SOURCE_TYPE_WEB
    assert entry["pdf_page"] is None


def test_changed_page_makes_the_index_stale(tmp_path: Path) -> None:
    engine = web_engine(tmp_path)
    unchanged = RAGIndex(tmp_path / "absent.pdf", tmp_path / "index.joblib").load(
        documents=load_web_documents(PAGES)
    )
    assert unchanged.metadata["chunks"] == engine.metadata["chunks"]

    edited = [dict(page) for page in PAGES]
    edited[0]["text"] = edited[0]["text"] + " Une mise a jour editoriale a ete publiee."
    reloaded = RAGIndex(tmp_path / "absent.pdf", tmp_path / "index.joblib").load(
        documents=load_web_documents(edited)
    )
    assert reloaded.manifest[0]["checksum"] != engine.manifest[0]["checksum"]


def test_empty_pages_are_ignored(tmp_path: Path) -> None:
    documents = load_web_documents([*PAGES, {"doc_id": "vide", "title": "", "text": "  "}])
    assert [document.doc_id for document in documents] == ["page-805", "page-808", "page-810"]


def test_mixed_corpus_keeps_pdf_and_web_locators_separate() -> None:
    pdf = Document(
        doc_id="rapport",
        source_type=SOURCE_TYPE_PDF,
        title="Rapport",
        segments=(Segment(unit=39, text="Contenu de la page.", pdf_page=39),),
    )
    registry = build_registry([pdf, *load_web_documents(PAGES)])
    assert registry[39]["locator"] == "p. PDF 39"
    assert registry[39]["url"] == ""
    assert registry[WEB_UNIT_OFFSET + 1]["title"] == "Organigramme"


def test_building_without_any_document_is_refused(tmp_path: Path) -> None:
    engine = RAGIndex(tmp_path / "absent.pdf", tmp_path / "index.joblib")
    with pytest.raises(RuntimeError):
        engine.build([])


def test_lettres_are_loaded_with_public_urls_and_page_locators() -> None:
    """Les Lettres extraites forment des documents citables par leur page publique."""
    documents = load_lettres_documents()
    assert documents, "Aucune Lettre extraite : lancez scripts/ocr_lettres_information.py"
    for document in documents:
        assert document.source_type == SOURCE_TYPE_LETTRE
        assert document.url.startswith("https://www.bcm.mr/")
        assert document.published_at
        assert all(segment.text.strip() for segment in document.segments)

    registry = build_registry(documents)
    first = documents[0].segments[0]
    entry = registry[first.unit]
    assert entry["locator"].startswith("Lettre d'information ")
    assert entry["locator"].endswith(f"p. {first.pdf_page}")


def test_lettre_units_never_collide_with_the_annual_report() -> None:
    """Aucune unité de Lettre ne peut désigner une page du rapport annuel."""
    documents = load_lettres_documents()
    units = [segment.unit for document in documents for segment in document.segments]
    assert len(units) == len(set(units))
    assert min(units) >= LETTRE_UNIT_OFFSET
    # Le rapport annuel compte 127 pages : la marge est de plusieurs ordres.
    assert min(units) > 1000


def test_citation_label_keeps_the_report_format_unchanged() -> None:
    """Le repère du rapport annuel ne doit pas changer : il est déjà cité partout."""
    assert citation_label(SOURCE_TYPE_PDF, "Rapport annuel", 39) == "p. PDF 39"
    assert (
        citation_label(SOURCE_TYPE_LETTRE, "Lettre d'information de la BCM — Mars 2026", 2)
        == "Lettre d'information Mars 2026, p. 2"
    )


def test_ocr_reading_order_follows_the_page_from_top_to_bottom() -> None:
    """Le moteur OCR situe l'origine en bas : un y décroissant descend la page."""
    items = [
        {"text": "deuxieme ligne", "x": 0.1, "y": 0.50, "width": 0.4, "height": 0.02},
        {"text": "titre", "x": 0.1, "y": 0.90, "width": 0.3, "height": 0.02},
        {"text": "fin", "x": 0.1, "y": 0.10, "width": 0.2, "height": 0.02},
    ]
    assert ocr_text_from_items(items).splitlines() == ["titre", "deuxieme ligne", "fin"]


def test_ocr_joins_fragments_of_one_line_from_left_to_right() -> None:
    items = [
        {"text": "monde", "x": 0.40, "y": 0.500, "width": 0.2, "height": 0.02},
        {"text": "Bonjour", "x": 0.10, "y": 0.502, "width": 0.2, "height": 0.02},
    ]
    assert ocr_text_from_items(items) == "Bonjour monde"


def test_ocr_removes_the_page_header_but_keeps_a_sentence_mentioning_it() -> None:
    """Le pictogramme d'en-tête part ; une phrase citant le site doit rester."""
    items = [
        {"text": "www.bcm.mr", "x": 0.05, "y": 0.97, "width": 0.09, "height": 0.01},
        {"text": "in", "x": 0.91, "y": 0.96, "width": 0.02, "height": 0.01},
        {
            "text": "site de la Banque Centrale (www.bcm.mr), ce premier",
            "x": 0.06,
            "y": 0.80,
            "width": 0.53,
            "height": 0.015,
        },
    ]
    assert ocr_text_from_items(items) == (
        "site de la Banque Centrale (www.bcm.mr), ce premier"
    )


def test_ocr_rejoins_a_word_split_across_two_lines() -> None:
    items = [
        {"text": "Banque Centrale de Mau-", "x": 0.06, "y": 0.80, "width": 0.5, "height": 0.015},
        {"text": "ritanie a organise une operation.", "x": 0.06, "y": 0.78, "width": 0.5, "height": 0.015},
    ]
    assert "Mauritanie" in ocr_text_from_items(items)


def test_api_answers_a_newsletter_question_with_a_clickable_source() -> None:
    """Bout en bout : une question propre aux Lettres est répondue et sourcée."""
    from api.app import create_app
    from core.config import get_settings

    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "OPEN_BROWSER": "false",
        }
    )
    response = create_app(settings_override=settings).test_client().post(
        "/api/ask",
        json={"question": "Que dit la lettre d'information de mars 2026 sur le don de sang ?"},
    )
    assert response.status_code == 200
    payload = response.json
    assert payload["grounded"] is True
    assert "don de sang" in payload["answer"].casefold()
    assert "p. PDF 20" not in payload["answer"], "Une unité interne ne doit jamais être citée."
    assert "Lettre d'information Mars 2026" in payload["answer"]

    lettre_sources = [
        source for source in payload["sources"] if source["source_type"] == "lettre"
    ]
    assert lettre_sources
    assert lettre_sources[0]["source_url"].startswith("https://www.bcm.mr/actualite/")
    assert lettre_sources[0]["citation"].startswith("Lettre d'information ")


def test_api_still_cites_the_annual_report_by_its_pdf_page() -> None:
    """L'ajout des Lettres ne change pas la citation du rapport annuel."""
    from api.app import create_app
    from core.config import get_settings

    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "OPEN_BROWSER": "false",
        }
    )
    response = create_app(settings_override=settings).test_client().post(
        "/api/ask",
        json={"question": "Quel est le taux de croissance du PIB réel en 2025 ?"},
    )
    assert response.status_code == 200
    assert "[p. PDF 21]" in response.json["answer"]
    assert response.json["sources"][0]["source_type"] == SOURCE_TYPE_PDF


def test_lettre_citation_is_translated_in_arabic() -> None:
    """Un repère français inséré dans une réponse arabe serait découpé par le bidi."""
    title = "Lettre d'information de la BCM — Mars 2026"
    assert citation_label(SOURCE_TYPE_LETTRE, title, 2, "ar") == (
        "الرسالة الإخبارية مارس 2026، ص. 2"
    )
    # Le repère du rapport annuel reste identique dans les deux langues : sa
    # forme latine courte est explicitement tolérée en arabe.
    assert citation_label(SOURCE_TYPE_PDF, "Rapport", 39, "ar") == "p. PDF 39"


def test_repair_rewrites_a_letter_page_cited_as_a_report_page() -> None:
    """Le modèle retombe parfois sur « p. PDF N » avec la page interne d'une Lettre."""
    from api.providers import _repair_source_confusion

    results = [
        {
            "pdf_page": 20202,
            "source_type": "lettre",
            "source_page": 2,
            "citation": "Lettre d'information Mars 2026, p. 2",
        }
    ]
    repaired = _repair_source_confusion("Une action citoyenne. [p. PDF 2]", results)
    assert repaired == "Une action citoyenne. [Lettre d'information Mars 2026, p. 2]"


def test_repair_leaves_a_genuine_report_page_untouched() -> None:
    from api.providers import _repair_source_confusion

    results = [
        {"pdf_page": 21, "source_type": "pdf", "source_page": 21, "citation": "p. PDF 21"},
        {
            "pdf_page": 20202,
            "source_type": "lettre",
            "source_page": 2,
            "citation": "Lettre d'information Mars 2026, p. 2",
        },
    ]
    assert _repair_source_confusion("Croissance. [p. PDF 21]", results) == (
        "Croissance. [p. PDF 21]"
    )


def test_repair_refuses_to_guess_between_two_letters() -> None:
    """Deux Lettres partageant le même numéro de page : la citation reste invalide."""
    from api.providers import _repair_source_confusion

    results = [
        {
            "pdf_page": 20202,
            "source_type": "lettre",
            "source_page": 2,
            "citation": "Lettre d'information Mars 2026, p. 2",
        },
        {
            "pdf_page": 20302,
            "source_type": "lettre",
            "source_page": 2,
            "citation": "Lettre d'information Avril 2026, p. 2",
        },
    ]
    # Inchangé : la validation en aval refusera la réponse plutôt que d'accréditer
    # une citation choisie au hasard.
    assert _repair_source_confusion("Texte. [p. PDF 2]", results) == "Texte. [p. PDF 2]"
