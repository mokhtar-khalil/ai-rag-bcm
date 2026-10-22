#!/usr/bin/env python3
"""Moteur OCR multiplateforme, interchangeable avec scripts/chart_ocr.swift.

Apple Vision n'existe que sur macOS : l'image Linux de production n'avait donc
aucun OCR. Ce script reproduit exactement le contrat de sortie du script Swift,
seul format que connaît api/charts.py :

    tesseract_ocr.py page.png  →  [{"text", "confidence", "x", "y", "width", "height"}, …]

Une entrée par ligne de texte ; coordonnées normalisées entre 0 et 1, origine en
bas à gauche comme dans Vision ; lignes triées de haut en bas puis de gauche à
droite. Seul l'exécutable `tesseract` est requis (paquet Debian tesseract-ocr),
aucune dépendance Python : la langue se règle par TESSERACT_LANG (« fra » par
défaut, le rapport annuel étant rédigé en français).
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from typing import Any


def parse_tesseract_tsv(tsv: str) -> list[dict[str, Any]]:
    """Regroupe les mots de la sortie TSV de Tesseract en lignes normalisées."""
    rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE))
    page = next((row for row in rows if row.get("level") == "1"), None)
    if page is None:
        return []
    page_width = float(page["width"]) or 1.0
    page_height = float(page["height"]) or 1.0

    lines: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        confidence = float(row.get("conf") or -1)
        if confidence < 0:
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        left, top = float(row["left"]), float(row["top"])
        right, bottom = left + float(row["width"]), top + float(row["height"])
        line = lines.setdefault(
            key,
            {"words": [], "confidences": [], "left": left, "top": top, "right": right, "bottom": bottom},
        )
        line["words"].append(text)
        line["confidences"].append(confidence)
        line["left"] = min(line["left"], left)
        line["top"] = min(line["top"], top)
        line["right"] = max(line["right"], right)
        line["bottom"] = max(line["bottom"], bottom)

    payload = [
        {
            "text": " ".join(line["words"]),
            "confidence": round(sum(line["confidences"]) / len(line["confidences"]) / 100, 3),
            "x": line["left"] / page_width,
            # Vision mesure y depuis le bas de l'image, Tesseract depuis le haut.
            "y": 1.0 - line["bottom"] / page_height,
            "width": (line["right"] - line["left"]) / page_width,
            "height": (line["bottom"] - line["top"]) / page_height,
        }
        for line in lines.values()
    ]
    # Même ordre de lecture que le script Swift : de haut en bas, puis de gauche
    # à droite pour les éléments situés sur une même bande horizontale.
    payload.sort(key=lambda item: (-round((item["y"] + item["height"] / 2) / 0.012), item["x"]))
    return payload


def main(arguments: list[str]) -> int:
    if len(arguments) != 2:
        sys.stderr.write("Usage: tesseract_ocr.py image.png\n")
        return 2
    language = os.environ.get("TESSERACT_LANG", "fra")
    try:
        process = subprocess.run(
            ["tesseract", arguments[1], "stdout", "-l", language, "--psm", "3", "tsv"],
            check=True,
            capture_output=True,
            text=True,
            timeout=70,
        )
    except FileNotFoundError:
        sys.stderr.write("Tesseract est introuvable (paquet tesseract-ocr).\n")
        return 3
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"Échec OCR: {exc.stderr}\n")
        return 5
    sys.stdout.write(json.dumps(parse_tesseract_tsv(process.stdout), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
