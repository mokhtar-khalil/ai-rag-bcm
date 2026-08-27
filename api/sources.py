"""Format pivot des sources indexées : rapports PDF et pages du site bcm.mr.

L'index historique ne connaissait qu'un seul PDF et identifiait chaque passage
par son numéro de page. Ce module généralise cette notion en « unité citable » :
une page PDF ou une page du site. La clé entière `unit` reste compatible avec
l'ancien `pdf_page` (une page PDF garde son propre numéro), ce qui préserve la
voie graphique et les réponses déjà servies au widget.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Les unités des sources non-PDF commencent au-delà de cette borne afin de ne
# jamais entrer en collision avec un numéro de page d'un rapport annuel.
WEB_UNIT_OFFSET = 10_000

# Chaque Lettre d'information reçoit sa propre plage d'unités : l'édition k
# occupe [20000 + k*100, 20000 + k*100 + 99], soit bien plus que ses 13 pages.
LETTRE_UNIT_OFFSET = 20_000
LETTRE_UNIT_STRIDE = 100

SOURCE_TYPE_PDF = "pdf"
SOURCE_TYPE_WEB = "web"
SOURCE_TYPE_LETTRE = "lettre"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LETTRES_DIR = PROJECT_ROOT / "data" / "lettres_information"

# Lignes d'habillage répétées sur chaque page des lettres : elles n'apportent
# aucune information et brouillent la recherche lexicale si on les conserve.
OCR_BOILERPLATE = {"www.bcm.mr", "bcm.mr", "in", "f", "x"}


@dataclass(frozen=True)
class Segment:
    """Fragment citable d'un document, porteur de son texte et de son repère."""

    unit: int
    text: str
    pdf_page: int | None = None
    heading: str = ""


@dataclass(frozen=True)
class Document:
    """Source normalisée, quelle que soit son origine (PDF ou site web)."""

    doc_id: str
    source_type: str
    title: str
    segments: tuple[Segment, ...]
    url: str = ""
    lang: str = "fr"
    published_at: str = ""
    updated_at: str = ""
    section: str = ""
    checksum: str = ""

    def manifest(self) -> dict[str, Any]:
        """Décrit le document pour la détection d'obsolescence de l'index."""
        return {
            "doc_id": self.doc_id,
            "source_type": self.source_type,
            "title": self.title,
            "url": self.url,
            "lang": self.lang,
            "updated_at": self.updated_at,
            "checksum": self.checksum,
            "segments": len(self.segments),
        }


def clean_pdf_page(text: str) -> str:
    """Nettoie le texte PDF sans supprimer les retours utiles aux tableaux."""
    text = text.replace("\x00", " ").replace("­", "")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            lines.append("")
            continue
        if line.casefold() == "rapport annuel 2025":
            continue
        if re.fullmatch(r"(?:\d{1,3}\s*){1,2}", line):
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def clean_web_text(text: str) -> str:
    """Normalise un contenu éditorial du site en conservant ses paragraphes."""
    text = text.replace("\x00", " ").replace("­", "")
    text = re.sub(r"[ \t ]+", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def file_checksum(path: Path) -> str:
    """Calcule l'empreinte d'un fichier afin de détecter un index obsolète."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def text_checksum(value: str) -> str:
    """Calcule l'empreinte d'un contenu textuel récupéré depuis le site."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_pdf_document(
    report_path: Path,
    doc_id: str = "rapport-annuel-2025",
    title: str = "Rapport annuel BCM - exercice 2025",
    url: str = "",
) -> Document:
    """Construit le document pivot correspondant à un rapport PDF complet."""
    from pypdf import PdfReader  # import local : dépendance lourde et optionnelle

    path = Path(report_path)
    if not path.exists():
        raise FileNotFoundError(f"Rapport introuvable : {path}")
    reader = PdfReader(str(path))
    segments = tuple(
        Segment(unit=number, text=clean_pdf_page(page.extract_text() or ""), pdf_page=number)
        for number, page in enumerate(reader.pages, start=1)
    )
    return Document(
        doc_id=doc_id,
        source_type=SOURCE_TYPE_PDF,
        title=title,
        segments=segments,
        url=url,
        checksum=file_checksum(path),
    )


def load_web_documents(records: Iterable[dict[str, Any]]) -> list[Document]:
    """Convertit les pages récupérées du site en documents pivots numérotés.

    Chaque enregistrement provient du connecteur d'ingestion et contient au
    minimum `doc_id`, `title`, `url` et `text`. Une page du site forme une seule
    unité citable : la citation renvoie vers son URL publique, pas vers un
    numéro de page.
    """
    documents: list[Document] = []
    for offset, record in enumerate(records):
        text = clean_web_text(str(record.get("text", "")))
        if not text:
            continue
        doc_id = str(record.get("doc_id") or record.get("url") or f"web-{offset}")
        documents.append(
            Document(
                doc_id=doc_id,
                source_type=SOURCE_TYPE_WEB,
                title=str(record.get("title", "")).strip(),
                segments=(
                    Segment(unit=WEB_UNIT_OFFSET + offset, text=text, pdf_page=None),
                ),
                url=str(record.get("url", "")).strip(),
                lang=str(record.get("lang", "fr")).strip() or "fr",
                published_at=str(record.get("published_at", "")).strip(),
                updated_at=str(record.get("updated_at", "")).strip(),
                section=str(record.get("section", "")).strip(),
                checksum=text_checksum(text),
            )
        )
    return documents


def build_registry(documents: Iterable[Document]) -> dict[int, dict[str, Any]]:
    """Associe chaque unité citable à sa source affichable par le widget."""
    registry: dict[int, dict[str, Any]] = {}
    for document in documents:
        for segment in document.segments:
            registry[segment.unit] = {
                "doc_id": document.doc_id,
                "source_type": document.source_type,
                "title": document.title,
                "url": document.url,
                "lang": document.lang,
                "published_at": document.published_at,
                "updated_at": document.updated_at,
                "pdf_page": segment.pdf_page,
                "locator": citation_label(
                    document.source_type, document.title, segment.pdf_page
                ),
            }
    return registry


# Le repère d'une Lettre nomme son mois. En arabe, laisser « Lettre
# d'information Mars 2026 » insérerait une phrase française au milieu du texte,
# que l'isolation bidi découperait mot à mot.
ARABIC_MONTHS = {
    "janvier": "يناير", "février": "فبراير", "fevrier": "فبراير", "mars": "مارس",
    "avril": "أبريل", "mai": "مايو", "juin": "يونيو", "juillet": "يوليو",
    "août": "أغسطس", "aout": "أغسطس", "septembre": "سبتمبر", "octobre": "أكتوبر",
    "novembre": "نوفمبر", "décembre": "ديسمبر", "decembre": "ديسمبر",
}


def _edition_label(title: str) -> str:
    """Isole « Mars 2026 » du titre complet d'une Lettre d'information."""
    return title.split("—")[-1].strip() if "—" in title else title.strip()


def _arabic_edition(edition: str) -> str:
    """Traduit « Mars 2026 » en « مارس 2026 » en conservant l'année."""
    parts = edition.split()
    return " ".join(ARABIC_MONTHS.get(part.casefold(), part) for part in parts)


def citation_label(
    source_type: str, title: str, pdf_page: int | None, language: str = "fr"
) -> str:
    """Compose le repère cité dans la réponse, lisible sans connaître l'index.

    Le rapport annuel conserve exactement `p. PDF N`, dans les deux langues : ce
    repère est déjà présent dans les réponses servies, dans l'historique des
    conversations et dans les tests de non-régression, et sa forme latine courte
    est explicitement tolérée en arabe. Les autres sources reçoivent un repère
    explicite, traduit lorsque la réponse est en arabe.
    """
    if source_type == SOURCE_TYPE_PDF:
        return f"p. PDF {pdf_page}"
    if source_type == SOURCE_TYPE_LETTRE:
        edition = _edition_label(title)
        if language == "ar":
            return f"الرسالة الإخبارية {_arabic_edition(edition)}، ص. {pdf_page}"
        return f"Lettre d'information {edition}, p. {pdf_page}"
    return title.strip()


def load_corpus(report_path: Path, ocr_dir: Path | None = None) -> list[Document]:
    """Assemble le corpus indexé : le rapport annuel et les Lettres extraites."""
    return [load_pdf_document(report_path), *load_lettres_documents(ocr_dir)]


def _is_boilerplate(item: dict[str, Any]) -> bool:
    """Reconnaît un habillage de page : adresse du site ou pictogramme social.

    La largeur sert de garde-fou : un fragment large qui commencerait par la même
    chaîne est du contenu, pas un en-tête.
    """
    text = str(item.get("text", "")).strip().casefold()
    return text in OCR_BOILERPLATE and float(item.get("width", 1.0)) < 0.12


def ocr_text_from_items(items: Iterable[dict[str, Any]]) -> str:
    """Reconstitue le texte d'une page à partir des fragments OCR positionnés.

    Le moteur local renvoie des fragments dont l'origine verticale est en bas de
    l'image : un `y` décroissant parcourt donc la page de haut en bas. Les
    fragments proches sur une même ligne sont regroupés, puis ordonnés de gauche
    à droite. Les Lettres d'information sont mises en page sur une seule colonne,
    ce qui rend cet ordre de lecture fidèle.
    """
    fragments = [
        item
        for item in items
        if isinstance(item, dict)
        and str(item.get("text", "")).strip()
        and not _is_boilerplate(item)
    ]
    if not fragments:
        return ""

    heights = sorted(float(item.get("height", 0.0)) for item in fragments)
    median_height = heights[len(heights) // 2] or 0.01
    tolerance = median_height * 0.6

    ordered = sorted(fragments, key=lambda item: (-float(item["y"]), float(item["x"])))
    lines: list[list[dict[str, Any]]] = [[ordered[0]]]
    for item in ordered[1:]:
        reference = float(lines[-1][0]["y"])
        if abs(float(item["y"]) - reference) <= tolerance:
            lines[-1].append(item)
        else:
            lines.append([item])

    rendered: list[str] = []
    for line in lines:
        line.sort(key=lambda item: float(item["x"]))
        text = " ".join(str(item["text"]).strip() for item in line).strip()
        if text:
            rendered.append(text)
    return clean_ocr_text("\n".join(rendered))


def clean_ocr_text(text: str) -> str:
    """Recolle les mots coupés en fin de ligne et retire les numéros isolés."""
    text = text.replace("\x00", " ").replace("­", "")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if re.fullmatch(r"\d{1,3}", line):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def load_lettres_documents(ocr_dir: Path | None = None) -> list[Document]:
    """Construit les documents pivots des Lettres d'information déjà extraites.

    Seuls les fichiers annexes produits par `scripts/ocr_lettres_information.py`
    sont lus : ni les PDF image, ni le moteur OCR ne sont nécessaires ici, ce qui
    permet d'indexer les lettres sur un serveur Linux.
    """
    directory = Path(ocr_dir) if ocr_dir is not None else LETTRES_DIR / "ocr"
    if not directory.is_dir():
        return []

    documents: list[Document] = []
    for position, path in enumerate(sorted(directory.glob("*.json"))):
        payload = json.loads(path.read_text(encoding="utf-8"))
        base = LETTRE_UNIT_OFFSET + position * LETTRE_UNIT_STRIDE
        segments = []
        for entry in payload.get("pages", []):
            text = clean_ocr_text(str(entry.get("text", "")))
            if not text:
                continue
            page = int(entry.get("page", 0))
            if not 1 <= page < LETTRE_UNIT_STRIDE:
                raise ValueError(
                    f"Page {page} hors de la plage réservée à l'édition {path.name}."
                )
            segments.append(Segment(unit=base + page, text=text, pdf_page=page))
        if not segments:
            continue
        documents.append(
            Document(
                doc_id=f"lettre-{payload.get('edition', path.stem)}",
                source_type=SOURCE_TYPE_LETTRE,
                title=str(payload.get("titre", path.stem)),
                segments=tuple(segments),
                url=str(payload.get("page_publique", "")),
                lang=str(payload.get("langue", "fr")) or "fr",
                published_at=str(payload.get("publie_le", "")),
                section="Lettre d'information",
                checksum=text_checksum(
                    "\n".join(segment.text for segment in segments)
                ),
            )
        )
    return documents


def corpus_fingerprint(report_path: Path, ocr_dir: Path | None = None) -> dict[str, str]:
    """Empreinte peu coûteuse du corpus, sans extraire le texte des documents.

    Comparer ces empreintes au démarrage évite de relire le PDF de 56 Mo et les
    fichiers OCR pour découvrir que rien n'a changé.
    """
    fingerprint: dict[str, str] = {}
    report = Path(report_path)
    if report.is_file():
        fingerprint[report.name] = file_checksum(report)
    directory = Path(ocr_dir) if ocr_dir is not None else LETTRES_DIR / "ocr"
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            fingerprint[f"ocr/{path.name}"] = file_checksum(path)
    return fingerprint
