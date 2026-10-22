"""Moteur OCR Tesseract de production : contrat de sortie et branchement.

Tesseract n'est pas requis pour ces tests : sa sortie TSV est simulée.
"""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path
from typing import Any

from api.app import create_app
from api.charts import _read_ocr_items, find_ocr_command
from core.config import PROJECT_ROOT, get_settings
from tests.conftest import generation_diffusee


SCRIPT_PATH = PROJECT_ROOT / "scripts" / "tesseract_ocr.py"
_spec = importlib.util.spec_from_file_location("tesseract_ocr", SCRIPT_PATH)
tesseract_ocr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tesseract_ocr)

HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext"
)
# Page de 1 000 × 2 000 px : un titre en haut, une ligne de texte plus bas.
SAMPLE_TSV = "\n".join(
    [
        HEADER,
        "1\t1\t0\t0\t0\t0\t0\t0\t1000\t2000\t-1\t",
        "4\t1\t1\t1\t1\t0\t100\t1800\t600\t40\t-1\t",
        "5\t1\t1\t1\t1\t1\t100\t1800\t200\t40\t90\tOpinion",
        "5\t1\t1\t1\t1\t2\t320\t1805\t380\t35\t80\tfavorable",
        "5\t1\t1\t1\t1\t3\t720\t1805\t10\t35\t-1\t",
        "5\t1\t2\t1\t1\t1\t50\t100\t300\t60\t95\tRapport",
        "5\t1\t2\t1\t1\t2\t360\t100\t500\t60\t93\tde l'Auditeur",
    ]
)


def test_tsv_lines_follow_the_apple_vision_contract() -> None:
    items = tesseract_ocr.parse_tesseract_tsv(SAMPLE_TSV)
    assert [item["text"] for item in items] == ["Rapport de l'Auditeur", "Opinion favorable"]
    title, body = items
    # Origine en bas à gauche, coordonnées normalisées, comme Vision.
    assert title["x"] == 0.05
    assert round(title["y"], 3) == 0.92
    assert round(title["width"], 3) == 0.81
    assert round(title["height"], 3) == 0.03
    assert round(body["y"], 3) == 0.08
    assert body["confidence"] == 0.85
    assert set(title) == {"text", "confidence", "x", "y", "width", "height"}


def test_empty_or_invalid_tsv_returns_no_line() -> None:
    assert tesseract_ocr.parse_tesseract_tsv("") == []
    assert tesseract_ocr.parse_tesseract_tsv(HEADER) == []


def test_script_is_executable_and_used_as_a_direct_ocr_command(tmp_path: Path) -> None:
    assert os.stat(SCRIPT_PATH).st_mode & stat.S_IXUSR
    # Un exécutable configuré est appelé directement, sans le script Swift.
    assert find_ocr_command(SCRIPT_PATH) == (str(SCRIPT_PATH), False)


def test_read_ocr_items_runs_the_command_and_caches_its_output(tmp_path: Path) -> None:
    fake_engine = tmp_path / "fake_ocr.py"
    fake_engine.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps([{'text': sys.argv[1][-8:], 'confidence': 1, "
        "'x': 0, 'y': 0, 'width': 1, 'height': 1}]))\n",
        encoding="utf-8",
    )
    fake_engine.chmod(0o755)
    image = tmp_path / "page.png"
    image.write_bytes(b"png")
    items = _read_ocr_items(image, str(fake_engine), False, tmp_path / "unused.swift")
    assert items[0]["text"] == "page.png"
    assert image.with_suffix(".ocr.json").is_file()


def test_document_ocr_follows_chart_analysis_unless_set_explicitly() -> None:
    base = {"APP_ENV": "test", "OPEN_BROWSER": "false"}
    assert get_settings({**base, "CHART_ANALYSIS_ENABLED": "true"}).document_ocr_enabled
    assert not get_settings({**base, "CHART_ANALYSIS_ENABLED": "false"}).document_ocr_enabled
    production_like = get_settings(
        {**base, "CHART_ANALYSIS_ENABLED": "false", "DOCUMENT_OCR_ENABLED": "true"}
    )
    assert production_like.document_ocr_enabled
    assert not production_like.chart_analysis_enabled


def test_scanned_section_is_read_by_document_ocr_without_chart_analysis(monkeypatch) -> None:
    """Configuration de production : OCR documentaire actif, graphiques inactifs."""
    captured: dict[str, Any] = {}
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
        captured["results"] = results
        return "L’auditeur exprime une opinion sans réserve [p. PDF 119]."

    monkeypatch.setattr("api.app.stream_answer", generation_diffusee(fake_answer))
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "SEMANTIC_RETRIEVAL": "false",
            "CHART_ANALYSIS_ENABLED": "false",
            "DOCUMENT_OCR_ENABLED": "true",
            "OPEN_BROWSER": "false",
        }
    )
    client = create_app(settings_override=settings).test_client()
    assert client.get("/health").json["document_ocr_enabled"] is True
    response = client.post(
        "/api/ask",
        json={
            "question": "C'est quoi le résumé de cette section ?",
            "language": "fr",
            "history": [
                {
                    "role": "user",
                    "content": "Y a-t-il un rapport de l'auditeur externe dans le rapport ?",
                },
                {
                    "role": "assistant",
                    "content": (
                        "Oui. Il comporte une section intitulée "
                        "« Rapport de l’Auditeur Externe ». [p. PDF 119]"
                    ),
                },
            ],
        },
    )
    assert response.status_code == 200
    assert response.json["answer"] == "L’auditeur exprime une opinion sans réserve [p. PDF 119]."
    assert captured["results"][0]["retrieval_mode"] == "document_ocr_local"
