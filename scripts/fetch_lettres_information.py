"""Récupère les Lettres d'information de la BCM et les reconditionne en PDF.

La BCM ne publie pas ces lettres sous forme de PDF : chaque édition est mise en
ligne comme une **image unique** (bandeau vertical ou page A4) attachée à une
actualité du back-office Drupal `bo.bcm.mr`, taguée `lettre_d_information`.
Ce script interroge l'API JSON du site, télécharge l'image d'origine et la
redécoupe en pages A4 pour produire un PDF citable, page par page, par l'index
du chatbot.

Le découpage cherche, autour de chaque coupe théorique, la bande de pixels la
plus uniforme : une marge ou un aplat de couleur. Cela évite de trancher une
ligne de texte au milieu, ce qui rendrait le passage inexploitable.

Usage :
    python scripts/fetch_lettres_information.py                 # éditions 2026
    python scripts/fetch_lettres_information.py --year 2025
    python scripts/fetch_lettres_information.py --lang ar --all
"""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "lettres_information"
CACHE_DIR = PROJECT_ROOT / "data" / "lettres_information" / ".cache"

API_HOST = "https://bo.bcm.mr"
PUBLIC_HOST = "https://www.bcm.mr"
TAG = "lettre_d_information"
USER_AGENT = "BCM-RAG-assistant/1.0 (indexation documentaire interne)"

# Ratio d'une page A4 portrait : la largeur de l'image d'origine est conservée,
# la hauteur de page en découle. Aucune image n'est redimensionnée.
A4_RATIO = 297 / 210
A4_WIDTH_MM = 210
MM_PER_INCH = 25.4

# Les bandeaux verticaux dépassent la limite anti-« decompression bomb » de
# Pillow ; la source est de confiance (site officiel de la BCM).
Image.MAX_IMAGE_PIXELS = 300_000_000

MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}
# Le nom accentué sert aux métadonnées et aux titres ; la clé non accentuée
# ci-dessus reste la forme retenue dans les noms de fichiers.
MONTH_LABELS = {
    1: "Janvier", 2: "Février", 3: "Mars", 4: "Avril", 5: "Mai", 6: "Juin",
    7: "Juillet", 8: "Août", 9: "Septembre", 10: "Octobre", 11: "Novembre",
    12: "Décembre",
}
MONTH_NAMES = {number: name for name, number in MONTHS.items()}


def strip_accents(value: str) -> str:
    """Réduit une chaîne à ses caractères ASCII, accents et apostrophes ôtés."""
    normalized = unicodedata.normalize("NFD", value)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def fetch_json(url: str) -> dict:
    """Appelle l'API JSON du back-office avec un agent identifiable."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.api+json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def list_editions(lang: str) -> list[dict]:
    """Liste les actualités taguées « lettre d'information », plus récentes d'abord."""
    query = urllib.parse.urlencode(
        {
            "filter[field_tag_actualites]": TAG,
            "sort": "-created",
            "page[limit]": 50,
        },
        quote_via=urllib.parse.quote,
    )
    payload = fetch_json(f"{API_HOST}/{lang}/jsonapi/node/actualites?{query}")
    return payload.get("data", [])


def resolve_image(node: dict, lang: str) -> dict | None:
    """Remonte de l'actualité vers le fichier image porteur de la lettre."""
    photo = node.get("field_photos_actualites")
    if not photo:
        return None
    media = fetch_json(
        f"{API_HOST}/{lang}/jsonapi/media/image/{photo['id']}?include=field_media_image"
    )
    field = media["data"].get("field_media_image") or {}
    path = (field.get("uri") or {}).get("url")
    if not path:
        return None
    return {
        "url": API_HOST + path,
        "filename": field.get("filename", ""),
        "filesize": field.get("filesize"),
        "width": (field.get("meta") or {}).get("width"),
        "height": (field.get("meta") or {}).get("height"),
    }


def parse_edition_period(title: str, created: str) -> tuple[int, int]:
    """Déduit le mois couvert par l'édition, sinon celui qui précède la parution.

    Le titre porte le mois de la lettre (« – Juillet 2026 »), qui diffère du
    mois de mise en ligne. En l'absence de mois explicite, on retient le mois
    précédant la publication, qui est la convention observée sur le site.
    """
    haystack = strip_accents(title).lower()
    match = re.search(rf"({'|'.join(MONTHS)})\s*(\d{{4}})", haystack)
    if match:
        return int(match.group(2)), MONTHS[match.group(1)]
    published = datetime.fromisoformat(created)
    year, month = published.year, published.month - 1
    if month == 0:
        year, month = year - 1, 12
    return year, month


def download(url: str, destination: Path) -> Path:
    """Télécharge l'image une seule fois : le cache évite de recharger 25 Mo."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        destination.write_bytes(response.read())
    return destination


def find_quiet_row(image: Image.Image, target: int, window: int) -> int:
    """Cherche autour de `target` la ligne la plus neutre où couper la page.

    Une bande de texte présente de forts contrastes horizontaux ; une marge ou
    un aplat de couleur non. On retient donc la ligne dont le voisinage a la
    variance horizontale la plus faible.
    """
    import numpy as np

    height = image.height
    low = max(1, target - window)
    high = min(height - 1, target + window)
    if high <= low:
        return min(target, height)

    band = np.asarray(image.convert("L").crop((0, low, image.width, high)), dtype=np.float32)
    row_variance = band.var(axis=1)
    # Une coupe propre suppose aussi que les lignes voisines se ressemblent :
    # on lisse sur quelques pixels pour écarter les frontières nettes.
    kernel = np.ones(5, dtype=np.float32) / 5
    smoothed = np.convolve(row_variance, kernel, mode="same")
    return low + int(smoothed.argmin())


def slice_to_a4(image: Image.Image) -> list[Image.Image]:
    """Découpe un bandeau vertical en pages A4 de largeur identique à la source."""
    width, height = image.size
    page_height = round(width * A4_RATIO)
    if height <= page_height * 1.1:
        return [pad_to_a4(image, width, page_height)]

    window = max(8, round(page_height * 0.10))
    pages: list[Image.Image] = []
    top = 0
    while top < height:
        target = top + page_height
        if height - target < page_height * 0.35:  # dernier reste : on ne coupe plus
            bottom = height
        else:
            bottom = find_quiet_row(image, target, window)
        pages.append(pad_to_a4(image.crop((0, top, width, bottom)), width, page_height))
        top = bottom
    return pages


def pad_to_a4(fragment: Image.Image, page_width: int, page_height: int) -> Image.Image:
    """Place un fragment sur une page A4 exacte, sans déformation ni rognage.

    Toutes les pages du PDF doivent partager la même géométrie, sinon le lecteur
    affiche un document à format variable. Un fragment plus haut qu'une A4 est
    donc réduit à l'échelle puis centré ; un fragment plus court est complété
    par un fond repris de sa dernière ligne, ce qui rend la jonction invisible
    sur les lettres à fond coloré.
    """
    fragment = fragment.convert("RGB")
    if fragment.size == (page_width, page_height):
        return fragment

    if fragment.height > page_height:
        ratio = min(page_width / fragment.width, page_height / fragment.height)
        fragment = fragment.resize(
            (max(1, round(fragment.width * ratio)), max(1, round(fragment.height * ratio))),
            Image.LANCZOS,
        )

    filler = fragment.crop((0, fragment.height - 1, fragment.width, fragment.height))
    page = filler.resize((page_width, page_height), Image.NEAREST)
    page.paste(fragment, ((page_width - fragment.width) // 2, 0))
    return page


def build_pdf(image_path: Path, pdf_path: Path, metadata: dict) -> int:
    """Écrit le PDF A4 de l'édition et renvoie son nombre de pages."""
    with Image.open(image_path) as source:
        pages = slice_to_a4(source)
    resolution = pages[0].width / (A4_WIDTH_MM / MM_PER_INCH)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(
        pdf_path,
        "PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=resolution,
        quality=88,
        title=metadata["title"],
        author="Banque Centrale de Mauritanie",
        subject=metadata["subject"],
        keywords="BCM, lettre d'information, Mauritanie, banque centrale",
        producer="scripts/fetch_lettres_information.py",
    )
    return len(pages)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2026, help="année des éditions à produire")
    parser.add_argument("--all", action="store_true", help="ignorer le filtre d'année")
    parser.add_argument("--lang", default="fr", choices=["fr", "ar"])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    output_dir = args.output if args.lang == "fr" else args.output / args.lang
    manifest: list[dict] = []

    for node in list_editions(args.lang):
        year, month = parse_edition_period(node["title"], node["created"])
        if not args.all and year != args.year:
            continue
        image = resolve_image(node, args.lang)
        if image is None:
            print(f"!! sans image : {node['title']}")
            continue

        suffix = Path(urllib.parse.unquote(image["url"])).suffix.lower() or ".png"
        cached = download(image["url"], CACHE_DIR / f"{node['drupal_internal__nid']}{suffix}")

        slug = f"BCM-lettre-information-{year}-{month:02d}-{MONTH_NAMES[month]}"
        pdf_path = output_dir / f"{slug}.pdf"
        title = f"Lettre d'information de la BCM — {MONTH_LABELS[month]} {year}"
        pages = build_pdf(
            cached,
            pdf_path,
            {"title": title, "subject": node.get("field_resume_actualites") or title},
        )

        alias = (node.get("path") or {}).get("alias") or ""
        manifest.append(
            {
                "fichier": pdf_path.name,
                "titre": title,
                "edition": f"{year}-{month:02d}",
                "pages": pages,
                "publie_le": node["created"][:10],
                "nid": node["drupal_internal__nid"],
                "page_publique": f"{PUBLIC_HOST}/actualite/{node['id']}{alias}",
                "image_source": image["url"],
                "image_dimensions": f"{image['width']}x{image['height']}",
                "langue": args.lang,
            }
        )
        print(f"OK  {pdf_path.name}  ({pages} pages, {pdf_path.stat().st_size / 1e6:.1f} Mo)")
        time.sleep(0.5)  # crawl volontairement lent, cf. docs/DEMANDE_ACCES_API_DRUPAL.md

    manifest.sort(key=lambda item: item["edition"])
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "source": "bo.bcm.mr — actualités taguées « lettre_d_information »",
                "recupere_le": datetime.now(timezone.utc).date().isoformat(),
                "editions": manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n{len(manifest)} édition(s) — manifeste : {manifest_path}")


if __name__ == "__main__":
    main()
