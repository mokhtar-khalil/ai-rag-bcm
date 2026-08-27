"""Détection, rendu, OCR et explication locale des graphiques du rapport BCM.

Les images restent sur la machine. Les coordonnées OCR normalisées servent à
relier titres, axes, périodes et valeurs sans inventer les éléments illisibles.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from api.query import build_retrieval_query
from core.language import response_language


CHART_MARKERS = (
    "graphique",
    "graphe",
    "organigramme",
    "courbe",
    "diagramme",
    "histogramme",
    "camembert",
    "figure",
    "visualisation",
    "رسم بياني",
    "الرسم البياني",
    "مخطط",
    "الشكل",
)

GENERIC_CHART_TERMS = {
    "annee",
    "annees",
    "courbe",
    "diagramme",
    "explique",
    "expliquer",
    "figure",
    "graphe",
    "graphique",
    "montre",
    "rapport",
    "2024",
    "2025",
}

# Emplacements usuels de Poppler quand pdftoppm n'est pas dans le PATH :
# Homebrew sur Apple Silicon, Homebrew sur Intel, puis MacPorts. Sur Linux et
# dans l'image Docker, le paquet poppler-utils l'installe dans le PATH et cette
# liste n'est jamais consultée. PDF_RENDERER_PATH reste la dérogation explicite.
FALLBACK_PDFTOPPM = (
    Path("/opt/homebrew/bin/pdftoppm"),
    Path("/usr/local/bin/pdftoppm"),
    Path("/opt/local/bin/pdftoppm"),
    # Certains environnements de développement fournissent Poppler dans un cache
    # d'outillage plutôt que dans le PATH. Le chemin est construit à partir du
    # répertoire personnel courant : il ne fige plus le nom d'un utilisateur.
    Path.home()
    / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdftoppm",
)


def _fold(value: str) -> str:
    """Normalise un texte pour comparer les libellés malgré accents et casse."""
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _terms(value: str) -> set[str]:
    """Extrait les termes distinctifs d'une question ou d'un graphique."""
    return {
        term
        for term in re.findall(r"[a-z0-9]{4,}", _fold(value))
        if term not in GENERIC_CHART_TERMS
    }


def financial_statement_kind(question: str) -> str | None:
    """Identifie l'un des quatre états financiers, malgré quelques fautes usuelles."""
    normalized = re.sub(r"[^a-z0-9]+", " ", _fold(question)).strip()
    statement_intent = any(
        marker in normalized
        for marker in ("etat", "l tat", "analyse", "analyser", "bilan")
    )
    if not statement_intent:
        return None
    if "situation financiere" in normalized:
        return "position"
    if (
        "resultat net" in normalized
        and (
            "resultat global" in normalized
            or "autres elements" in normalized
        )
    ):
        return "comprehensive_income"
    if "variations" in normalized and "capitaux propres" in normalized:
        return "equity_changes"
    if "flux" in normalized and "tresorerie" in normalized:
        return "cash_flows"
    return None


def is_financial_position_question(question: str) -> bool:
    """Indique si la question vise spécifiquement le bilan comparatif."""
    return financial_statement_kind(question) == "position"


def is_chart_question(question: str) -> bool:
    """Détecte les demandes explicites ou implicites portant sur un graphique."""
    folded = _fold(question)
    if any(marker in folded for marker in CHART_MARKERS):
        return True
    monthly = bool(
        re.search(r"\b(?:par mois|mensuel(?:le|les|s)?|mensuellement)\b", folded)
        or any(marker in folded for marker in ("شهريا", "شهرياً", "حسب الشهر", "كل شهر"))
    )
    measurable = any(
        marker in folded
        for marker in (
            "evolution",
            "nombre",
            "montant",
            "transaction",
            "transfert",
            "valeur",
            "virement",
            "volume",
            "حجم",
            "قيمة",
            "تحويل",
            "معاملة",
            "تطور",
        )
    )
    currency_evolution = (
        "evolution" in folded
        and "devise" in folded
        and "achat" in folded
        and "vente" in folded
    )
    financial_statement = financial_statement_kind(question) is not None
    return (monthly and measurable) or currency_evolution or financial_statement


def is_chart_existence_question(question: str) -> bool:
    """Détecte une question qui demande seulement si un graphique existe."""
    folded = _fold(question)
    normalized = re.sub(r"[^a-z0-9]+", " ", folded).strip()
    french_existence = any(
        marker in normalized
        for marker in (
            "y a t il",
            "y a t elle",
            "existe t il",
            "existe t elle",
            "est ce qu il existe",
            "est ce qu elle existe",
            "trouve t on",
            "le rapport contient il",
            "le rapport contient elle",
        )
    )
    arabic_existence = any(
        marker in folded for marker in ("هل يوجد", "هل يحتوي التقرير", "هل هناك")
    )
    return french_existence or arabic_existence


def enrich_chart_query(question: str) -> str:
    """Ajoute des termes de structure visuelle, jamais des valeurs ou une réponse."""
    if not is_chart_question(question):
        return question
    folded = _fold(question)
    domain_terms: list[str] = []
    if "depot" in folded and "credit" in folded:
        domain_terms.extend(
            ["secteur bancaire", "coefficient transformation", "clientèle"]
        )
    if "liquidite bancaire" in folded:
        domain_terms.extend(
            ["réserves libres", "réserves obligatoires", "open market"]
        )
    if "solvabilite" in folded:
        domain_terms.extend(["fonds propres", "actifs pondérés", "ratio"])
    if "virement" in folded or "transfert" in folded:
        domain_terms.extend(["ACH", "activité des paiements", "transferts"])
    if "تحويل" in folded or "معاملة" in folded:
        domain_terms.extend(
            ["ACH", "virements volume et valeur par mois", "activité des paiements"]
        )
    if "devise" in folded and "achat" in folded and "vente" in folded:
        domain_terms.extend(
            ["change manuel", "Euro USD", "évolution trimestrielle"]
        )
    if "organigramme" in folded:
        # Ces libellés figurent dans l'image et orientent le retrieval vers la
        # bonne page, sans fournir au système une réponse préconstruite.
        domain_terms.extend(
            [
                "organisation hiérarchie",
                "Gouverneur Gouverneurs Adjoints",
                "conseils comités directions générales",
            ]
        )
    statement_kind = financial_statement_kind(question)
    if statement_kind == "position":
        domain_terms.extend(
            [
                "états financiers bilan comptable",
                "total des actifs total des passifs capitaux propres",
                "31/12/2025 31/12/2024 chiffres en MRU",
            ]
        )
    elif statement_kind == "comprehensive_income":
        domain_terms.extend(
            [
                "état du résultat net autres éléments du résultat global",
                "produit net bancaire coût du risque résultat de change",
                "31/12/2025 31/12/2024 chiffres en MRU",
            ]
        )
    elif statement_kind == "equity_changes":
        domain_terms.extend(
            [
                "état des variations des capitaux propres",
                "solde réserves résultats reportés dividendes",
                "31/12/2025 31/12/2024 chiffres en MRU",
            ]
        )
    elif statement_kind == "cash_flows":
        domain_terms.extend(
            [
                "état des flux de trésorerie",
                "activité exploitation investissement financement variation nette",
                "31/12/2025 31/12/2024 chiffres en MRU",
            ]
        )
    suffix = " ".join(domain_terms)
    return f"{question} {suffix} graphique évolution légende tendance".strip()


def explicit_chart_numbers(question: str) -> set[int]:
    """Extrait les numéros de graphiques explicitement cités par l'utilisateur."""
    return {
        int(value)
        for value in re.findall(
            r"(?:graphique|graphe|figure)\s*(?:n[°ºo]\s*)?(\d{1,3})",
            _fold(question),
        )
    }


def select_chart_pages(
    question: str,
    results: Iterable[dict[str, Any]],
    maximum: int = 2,
) -> list[int]:
    """Sélectionne des pages visuellement pertinentes sans utiliser leur image."""
    if maximum <= 0 or not is_chart_question(question):
        return []
    wanted_numbers = explicit_chart_numbers(question)
    query_terms = _terms(question)
    candidates: list[tuple[float, int]] = []

    for rank, item in enumerate(results):
        page = int(item["pdf_page"])
        if page <= 12:
            continue
        text = str(item.get("text", ""))
        folded_text = _fold(re.sub(r"\s+", " ", text))
        organization_requested = "organigramme" in _fold(question)
        statement_kind = financial_statement_kind(question)
        # Une page voisine peut décrire la gouvernance sans contenir le schéma.
        # Pour cette intention précise, seule la page qui porte réellement le
        # mot « Organigramme » doit être rendue et analysée.
        if organization_requested and "organigramme" not in folded_text:
            continue
        if statement_kind:
            required_titles = {
                "position": ("etat de la situation financiere",),
                "comprehensive_income": (
                    "etat du resultat net",
                    "autres elements du resultat global",
                ),
                "equity_changes": ("etat des variations des capitaux propres",),
                "cash_flows": ("etat des flux de tresorerie",),
            }
            if not all(
                marker in folded_text for marker in required_titles[statement_kind]
            ) or "chiffres en mru" not in folded_text:
                continue
            if statement_kind != "cash_flows" and len(text) > 300:
                continue
        chart_numbers = {
            int(value)
            for value in re.findall(r"graphique\s*(\d{1,3})", folded_text)
        }
        if wanted_numbers and not (wanted_numbers & chart_numbers):
            continue

        overlap = len(query_terms & _terms(text))
        chart_marker = bool(
            chart_numbers
            or "graphique" in folded_text
            or "organigramme" in folded_text
            or "etat de la situation financiere" in folded_text
            or "etat du resultat net" in folded_text
            or "etat des variations des capitaux propres" in folded_text
            or "etat des flux de tresorerie" in folded_text
        )
        visual_language = any(
            marker in folded_text
            for marker in ("evolution", "repartition", "structure", "composition")
        )
        numeric_density = min(len(re.findall(r"\d", text)) / 40.0, 1.0)
        score = float(item.get("score", 0.0)) + overlap * 1.6 - rank * 0.015
        # Un simple mot « graphique » ne doit pas battre une page beaucoup plus
        # proche du sujet : certains graphiques sont des images sans texte natif.
        score += 0.6 if chart_marker else 0.0
        score += 0.5 if visual_language else 0.0
        score += numeric_density
        if organization_requested and "organigramme" in folded_text:
            score += 10.0
        if statement_kind:
            score += 10.0
        if wanted_numbers & chart_numbers:
            score += 20.0
        if not chart_marker and overlap < 2:
            continue
        candidates.append((score, page))

    selected: list[int] = []
    for _, page in sorted(candidates, reverse=True):
        if page in selected:
            continue
        selected.append(page)
        if len(selected) >= maximum:
            break
    return selected


def find_pdftoppm(configured_path: str = "") -> Path | None:
    """Localise l'outil qui transforme une page PDF en image PNG."""
    if configured_path:
        candidate = Path(configured_path).expanduser()
        if candidate.is_file():
            return candidate
    discovered = shutil.which("pdftoppm")
    if discovered:
        return Path(discovered)
    for candidat in FALLBACK_PDFTOPPM:
        if candidat.is_file():
            return candidat
    return None


def render_chart_pages(
    report_path: Path,
    pages: Iterable[int],
    cache_path: Path,
    dpi: int = 170,
    renderer_path: str = "",
) -> list[dict[str, Any]]:
    """Rend au plus les pages demandées avec Poppler et réutilise le cache local."""
    renderer = find_pdftoppm(renderer_path)
    if renderer is None:
        raise RuntimeError("Le moteur de rendu PDF pdftoppm est introuvable.")
    report = Path(report_path)
    if not report.is_file():
        raise FileNotFoundError(f"Rapport introuvable : {report}")
    cache = Path(cache_path)
    cache.mkdir(parents=True, exist_ok=True)
    stat = report.stat()
    fingerprint = f"{stat.st_size:x}-{stat.st_mtime_ns:x}"
    rendered: list[dict[str, Any]] = []

    for raw_page in pages:
        page = int(raw_page)
        if page < 1:
            continue
        output = cache / f"{fingerprint}-page-{page}.png"
        if not output.is_file() or output.stat().st_size < 1024:
            prefix = output.with_suffix("")
            subprocess.run(
                [
                    str(renderer),
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-png",
                    "-r",
                    str(dpi),
                    "-singlefile",
                    str(report),
                    str(prefix),
                ],
                check=True,
                capture_output=True,
                timeout=45,
            )
        if not output.is_file() or output.stat().st_size < 1024:
            raise RuntimeError(f"Le rendu de la page PDF {page} a échoué.")
        rendered.append({"pdf_page": page, "path": output})
    return rendered


def find_ocr_command(configured_path: Path) -> tuple[str, bool] | None:
    """Localise l'exécutable OCR ou le script Swift local configuré."""
    candidate = Path(configured_path).expanduser()
    if candidate.is_file():
        return str(candidate), False
    swift = shutil.which("swift")
    return (swift, True) if swift else None


def _ocr_item_score(
    text: str, query_terms: set[str], wanted_numbers: set[int]
) -> float:
    """Attribue un score à un libellé OCR selon la question et sa position."""
    folded = _fold(text)
    terms = _terms(text)
    score = len(query_terms & terms) * 6.0
    if any(marker in folded for marker in CHART_MARKERS):
        score += 3.0
    if any(
        marker in folded
        for marker in (
            "evolution",
            "repartition",
            "structure",
            "composition",
            "organigramme",
            "situation financiere",
            "etat du resultat net",
            "variations des capitaux",
            "flux de tresorerie",
        )
    ):
        score += 2.0
    if re.search(r"\d", text):
        score += 0.6
    for number in wanted_numbers:
        if re.search(rf"(?:graphique|graphe|figure)\s*{number}\b", folded):
            score += 20.0
    return score


def _format_ocr_context(
    question: str,
    page: int,
    items: list[dict[str, Any]],
    maximum_chars: int,
) -> str:
    """Transforme la sortie OCR brute en contexte textuel borné et positionné."""
    # Un bilan contient davantage de lignes qu'un graphique courant. Garder la
    # section basse est indispensable pour lire les passifs et capitaux propres.
    if financial_statement_kind(question):
        maximum_chars = max(maximum_chars, 9_000)
    query_terms = _terms(question)
    wanted_numbers = explicit_chart_numbers(question)
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in items:
        text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        if not text:
            continue
        scored.append((_ocr_item_score(text, query_terms, wanted_numbers), item))

    title_candidates: list[tuple[float, dict[str, Any]]] = []
    numbered_labels: list[dict[str, Any]] = []
    for score, item in scored:
        text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        folded = _fold(text)
        if re.fullmatch(r"(?:graphique|graphe|figure)\s*\d+\s*: ?", folded):
            numbered_labels.append(item)
            continue
        if len(text) <= 170 and any(
            marker in folded
            for marker in (
                "evolution",
                "repartition",
                "structure",
                "composition",
                "par mois",
                "mensuel",
                "volume et valeur",
                "organigramme",
                "situation financiere",
                "etat du resultat net",
                "variations des capitaux",
                "flux de tresorerie",
                "etat du resultat net",
                "variations des capitaux",
                "flux de tresorerie",
            )
        ):
            title_score = score + len(query_terms & _terms(text)) * 4.0
            for label in numbered_labels:
                label_text = _fold(str(label.get("text", "")))
                if not any(
                    re.search(rf"\b{number}\b", label_text)
                    for number in wanted_numbers
                ):
                    continue
                if (
                    abs(float(label.get("x", 0.0)) - float(item.get("x", 0.0))) < 0.18
                    and 0 <= float(label.get("y", 0.0)) - float(item.get("y", 0.0)) < 0.09
                ):
                    title_score += 30.0
            title_candidates.append((title_score, item))

    # Les libellés "Graphique N" peuvent apparaître avant le titre dans l'ordre
    # OCR ; on refait le bonus de proximité une fois tous les éléments connus.
    for index, (score, item) in enumerate(title_candidates):
        bonus = 0.0
        for label in numbered_labels:
            label_text = _fold(str(label.get("text", "")))
            if not any(
                re.search(rf"\b{number}\b", label_text) for number in wanted_numbers
            ):
                continue
            if (
                abs(float(label.get("x", 0.0)) - float(item.get("x", 0.0))) < 0.18
                and 0 <= float(label.get("y", 0.0)) - float(item.get("y", 0.0)) < 0.09
            ):
                bonus = 30.0
        title_candidates[index] = (score + bonus, item)

    selected: list[dict[str, Any]] = []
    if title_candidates:
        _, title_item = max(title_candidates, key=lambda value: value[0])
        title_x = float(title_item.get("x", 0.0))
        title_y = float(title_item.get("y", 0.0))
        title_width = float(title_item.get("width", 0.0))
        center_x = title_x + title_width / 2
        if title_width >= 0.48:
            left, right = 0.0, 1.0
        elif center_x < 0.5:
            left, right = 0.0, 0.5
        else:
            left, right = 0.5, 1.0

        lower_bound = 0.015
        # Un état financier occupe toute une demi-page et son titre peut être
        # coupé sur deux lignes. Dans ce cas, une seconde ligne de titre ne doit
        # pas être interprétée comme le début d'un autre visuel.
        if financial_statement_kind(question) is None:
            boundary_candidates: list[float] = []
            for _, item in scored:
                if item is title_item:
                    continue
                item_y = float(item.get("y", 0.0))
                item_center_x = float(item.get("x", 0.0)) + float(
                    item.get("width", 0.0)
                ) / 2
                text = _fold(str(item.get("text", "")))
                if not (left <= item_center_x <= right and item_y < title_y - 0.035):
                    continue
                if any(
                    marker in text
                    for marker in (
                        "evolution",
                        "repartition",
                        "structure",
                        "composition",
                        "par mois",
                        "mensuel",
                        "volume et valeur",
                        "organigramme",
                        "situation financiere",
                    )
                ) or re.match(r"^\d+(?:\.\d+){1,4}-", text):
                    boundary_candidates.append(item_y)
            if boundary_candidates:
                lower_bound = max(boundary_candidates) + 0.018

        for _, item in scored:
            item_center_x = float(item.get("x", 0.0)) + float(
                item.get("width", 0.0)
            ) / 2
            item_y = float(item.get("y", 0.0))
            if left <= item_center_x <= right and lower_bound <= item_y <= title_y + 0.035:
                selected.append(item)
        if title_item not in selected:
            selected.append(title_item)

    seeds = [item for score, item in scored if score >= 5.0]
    if not seeds:
        seeds = [item for _, item in sorted(scored, key=lambda value: value[0], reverse=True)[:4]]
    if not selected:
        for score, item in scored:
            x = float(item.get("x", 0.0)) + float(item.get("width", 0.0)) / 2
            y = float(item.get("y", 0.0)) + float(item.get("height", 0.0)) / 2
            near_seed = any(
                abs(x - (float(seed.get("x", 0.0)) + float(seed.get("width", 0.0)) / 2))
                <= 0.52
                and abs(
                    y
                    - (
                        float(seed.get("y", 0.0))
                        + float(seed.get("height", 0.0)) / 2
                    )
                )
                <= 0.38
                for seed in seeds
            )
            if score > 0.0 or near_seed:
                selected.append(item)

    selected.sort(
        key=lambda item: (
            -round(float(item.get("y", 0.0)), 2),
            float(item.get("x", 0.0)),
        )
    )
    lines = [
        f"[OCR local de la page PDF {page}; coordonnées normalisées x/y]",
        "Utiliser les positions seulement pour relier titres, séries, années et valeurs.",
    ]
    for item in selected:
        text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        x = float(item.get("x", 0.0))
        y = float(item.get("y", 0.0))
        line = f"- x={x:.3f} y={y:.3f} : {text}"
        if len("\n".join([*lines, line])) > maximum_chars:
            break
        lines.append(line)
    return "\n".join(lines)


def _document_ocr_context(
    page: int,
    items: list[dict[str, Any]],
    maximum_chars: int = 18_000,
) -> str:
    """Reconstitue le texte courant d'une page scannée, colonne par colonne.

    Certaines pages du rapport, notamment le rapport de l'auditeur, ne
    contiennent qu'un titre dans la couche texte du PDF. Cette fonction traite
    alors l'OCR comme un document, et non comme un graphique : elle préserve
    l'ordre de lecture et sépare les deux colonnes éventuelles.
    """
    cleaned: list[dict[str, Any]] = []
    for item in items:
        text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        folded = _fold(text)
        if not text or folded == "rapport annuel 2025" or re.fullmatch(r"\d{1,3}", text):
            continue
        cleaned.append({**item, "text": text})
    if not cleaned:
        return ""

    # Les doubles pages du rapport sont placées côte à côte. Le seuil médian
    # évite de mélanger une phrase de la page gauche avec celle de droite.
    has_left = any(
        float(item.get("x", 0.0)) + float(item.get("width", 0.0)) / 2 < 0.47
        for item in cleaned
    )
    has_right = any(
        float(item.get("x", 0.0)) + float(item.get("width", 0.0)) / 2 > 0.53
        for item in cleaned
    )
    columns = ("gauche", "droite") if has_left and has_right else ("page",)
    lines = [f"[OCR documentaire local de la page PDF {page}]"]
    for column in columns:
        if column == "page":
            selected = cleaned
        else:
            left_column = column == "gauche"
            selected = [
                item
                for item in cleaned
                if (
                    float(item.get("x", 0.0))
                    + float(item.get("width", 0.0)) / 2
                    < 0.5
                )
                == left_column
            ]
        selected.sort(
            key=lambda item: (
                -round(float(item.get("y", 0.0)), 3),
                float(item.get("x", 0.0)),
            )
        )
        if len(columns) > 1:
            lines.append(f"[Colonne {column}]")
        # Apple Vision renvoie souvent une ligne visuelle par objet. On rassemble
        # ces lignes jusqu'à la ponctuation finale afin que le générateur reçoive
        # de vrais paragraphes et non des fragments indépendants.
        paragraphs: list[str] = []
        buffer: list[str] = []
        for item in selected:
            value = str(item["text"]).strip()
            buffer.append(value)
            joined = " ".join(buffer)
            if re.search(r"[.!?;][»”']?$", value) or len(joined) >= 850:
                paragraphs.append(joined)
                buffer = []
        if buffer:
            paragraphs.append(" ".join(buffer))
        for paragraph in paragraphs:
            candidate = f"- {paragraph}"
            if len("\n".join([*lines, candidate])) > maximum_chars:
                return "\n".join(lines)
            lines.append(candidate)
    return "\n".join(lines)


def _read_ocr_items(
    image_path: Path,
    command: str,
    uses_swift_script: bool,
    script_path: Path,
) -> list[dict[str, Any]]:
    """Lit le cache OCR d'une image ou exécute le moteur local une seule fois."""
    cache_path = image_path.with_suffix(".ocr.json")
    if cache_path.is_file() and cache_path.stat().st_size > 2:
        items = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        arguments = (
            [command, str(script_path), str(image_path)]
            if uses_swift_script
            else [command, str(image_path)]
        )
        process = subprocess.run(
            arguments,
            check=True,
            capture_output=True,
            text=True,
            timeout=75,
        )
        items = json.loads(process.stdout)
        cache_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    if not isinstance(items, list):
        raise RuntimeError("La sortie OCR locale n'est pas une liste valide.")
    return items


def extract_chart_contexts(
    question: str,
    rendered_pages: Iterable[dict[str, Any]],
    script_path: Path,
    ocr_path: Path,
    maximum_chars: int = 5000,
) -> list[dict[str, Any]]:
    """Extrait localement les libellés et valeurs ; aucune image n'est retournée."""
    command_info = find_ocr_command(ocr_path)
    script = Path(script_path)
    if command_info is None or not script.is_file():
        raise RuntimeError("Le moteur OCR local ou son script est introuvable.")
    command, uses_swift_script = command_info
    contexts: list[dict[str, Any]] = []

    for rendered in rendered_pages:
        page = int(rendered["pdf_page"])
        image_path = Path(rendered["path"])
        items = _read_ocr_items(
            image_path, command, uses_swift_script, script
        )
        text = _format_ocr_context(
            question, page, items, maximum_chars=maximum_chars
        )
        if len(text.splitlines()) <= 2:
            continue
        contexts.append(
            {
                "chunk_id": -(10_000 + page),
                "pdf_page": page,
                "text": text,
                "score": 1.0,
                "lexical_score": 1.0,
                "semantic_score": None,
                "retrieval_mode": "chart_ocr_local",
                "kind": "chart_ocr",
                "image_path": str(image_path),
                "keyword_overlap": len(_terms(question) & _terms(text)),
                "query_keyword_count": max(len(_terms(question)), 1),
            }
        )
    return contexts


def extract_document_page_contexts(
    rendered_pages: Iterable[dict[str, Any]],
    script_path: Path,
    ocr_path: Path,
    maximum_chars: int = 18_000,
) -> list[dict[str, Any]]:
    """Extrait le texte de pages scannées citées dans la conversation.

    Contrairement à :func:`extract_chart_contexts`, cette voie ne sélectionne
    pas des axes ou des valeurs. Elle restitue le contenu narratif complet afin
    de résumer une section précédemment mentionnée.
    """
    command_info = find_ocr_command(ocr_path)
    script = Path(script_path)
    if command_info is None or not script.is_file():
        raise RuntimeError("Le moteur OCR local ou son script est introuvable.")
    command, uses_swift_script = command_info
    contexts: list[dict[str, Any]] = []
    for rendered in rendered_pages:
        page = int(rendered["pdf_page"])
        image_path = Path(rendered["path"])
        items = _read_ocr_items(
            image_path, command, uses_swift_script, script
        )
        text = _document_ocr_context(page, items, maximum_chars=maximum_chars)
        if len(text.splitlines()) <= 1:
            continue
        contexts.append(
            {
                "chunk_id": -(20_000 + page),
                "pdf_page": page,
                "text": text,
                "score": 1.0,
                "lexical_score": 1.0,
                "semantic_score": None,
                "retrieval_mode": "document_ocr_local",
                "kind": "document_ocr",
                "image_path": str(image_path),
                "keyword_overlap": 0,
                "query_keyword_count": 1,
            }
        )
    return contexts


def _context_ocr_lines(context: dict[str, Any]) -> list[str]:
    """Retire les coordonnées d'un contexte OCR pour récupérer ses libellés."""
    lines: list[str] = []
    for raw_line in str(context.get("text", "")).splitlines():
        match = re.match(r"- x=[0-9.]+ y=[0-9.]+ : (.+)", raw_line)
        if match:
            lines.append(match.group(1).strip())
    return lines


def _supporting_sentences(
    question: str,
    pages: set[int],
    results: Iterable[dict[str, Any]],
    maximum: int = 3,
) -> list[tuple[str, int]]:
    """Sélectionne sur les mêmes pages le commentaire narratif du graphique."""
    query_terms = _terms(question)
    ranked: list[tuple[float, str, int]] = []
    for item in results:
        page = int(item["pdf_page"])
        if page not in pages:
            continue
        compact = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý])", compact):
            sentence = sentence.strip()
            if not 45 <= len(sentence) <= 520:
                continue
            if not sentence[0].isupper() or re.search(r"\b\d+(?:\.\d+){2,}-", sentence):
                continue
            folded = _fold(sentence)
            overlap = len(query_terms & _terms(sentence))
            score = overlap * 3.0
            if re.search(r"\d", sentence):
                score += 0.7
            if any(
                marker in folded
                for marker in (
                    "augmente",
                    "baisse",
                    "diminue",
                    "evolution",
                    "hausse",
                    "progress",
                    "recul",
                    "stable",
                    "atteint",
                    "etabli",
                )
            ):
                score += 1.2
            ranked.append((score, sentence, page))

    selected: list[tuple[str, int]] = []
    seen: set[str] = set()
    for score, sentence, page in sorted(ranked, reverse=True):
        if score <= 0:
            continue
        key = _fold(sentence)[:100]
        if key in seen:
            continue
        selected.append((sentence, page))
        seen.add(key)
        if len(selected) >= maximum:
            break
    return selected


def _ocr_positioned_items(contexts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconvertit les lignes OCR en objets texte-position exploitables."""
    items: list[dict[str, Any]] = []
    pattern = re.compile(r"- x=([0-9.]+) y=([0-9.]+) : (.+)")
    for context in contexts:
        for raw_line in str(context.get("text", "")).splitlines():
            match = pattern.fullmatch(raw_line)
            if match:
                items.append(
                    {
                        "x": float(match.group(1)),
                        "y": float(match.group(2)),
                        "text": match.group(3).strip(),
                        "pdf_page": int(context["pdf_page"]),
                    }
                )
    return items


def _number_value(text: str) -> float | None:
    """Convertit prudemment un libellé OCR qui contient uniquement un nombre."""
    compact = text.strip().replace(" ", ".").replace(",", ".")
    compact = compact.strip("-%")
    if not re.fullmatch(r"\d+(?:\.\d+)?", compact):
        return None
    try:
        return float(compact)
    except ValueError:
        return None


def _deposit_credit_intermediation_summary(
    title: str,
    contexts: list[dict[str, Any]],
) -> list[str]:
    """Analyse le graphique connu sur dépôts, crédits et intermédiation."""
    folded_title = _fold(title)
    if not all(term in folded_title for term in ("depot", "credit", "intermediation")):
        return []
    items = _ocr_positioned_items(contexts)
    years = {
        int(item["text"]): item
        for item in items
        if re.fullmatch(r"20(?:24|25)", item["text"])
    }
    values_by_year: dict[int, tuple[float, float, float]] = {}
    for year, year_item in years.items():
        year_x = float(year_item["x"])
        year_y = float(year_item["y"])
        candidates: list[tuple[float, float, float]] = []
        for item in items:
            if float(item["y"]) <= year_y + 0.04:
                continue
            value = _number_value(str(item["text"]))
            if value is None or not 0 <= value < 500:
                continue
            delta_x = float(item["x"]) - year_x
            if abs(delta_x) <= 0.032:
                candidates.append((delta_x, float(item["y"]), value))
        deposits = [value for dx, _, value in candidates if dx < 0 and value > 100]
        credits = [value for dx, _, value in candidates if dx >= 0 and value > 100]
        ratios = [
            (y, value)
            for _, y, value in candidates
            if 50 <= value <= 100
        ]
        if deposits and credits and ratios:
            values_by_year[year] = (
                max(deposits),
                max(credits),
                max(ratios)[1],
            )
    if 2024 not in values_by_year or 2025 not in values_by_year:
        return []
    d24, c24, i24 = values_by_year[2024]
    d25, c25, i25 = values_by_year[2025]
    deposit_growth = (d25 / d24 - 1) * 100
    credit_growth = (c25 / c24 - 1) * 100

    def french(value: float, decimals: int = 1) -> str:
        """Formate localement un nombre avec la virgule décimale française."""
        return f"{value:.{decimals}f}".replace(".", ",")

    return [
        f"Les dépôts passent de {french(d24)} à {french(d25)} milliards de MRU "
        f"(+{french(deposit_growth)} %).",
        f"Les crédits passent de {french(c24)} à {french(c25)} milliards de MRU "
        f"(+{french(credit_growth)} %).",
        f"Le taux d’intermédiation recule de {i24:.0f} % à {i25:.0f} %, soit "
        f"{abs(i25 - i24):.0f} points : les dépôts progressent donc plus vite que les crédits.",
    ]


def _monthly_volume_summary(
    question: str,
    title: str,
    contexts: list[dict[str, Any]],
    language: str | None = None,
) -> list[str]:
    """Lit localement les hauteurs de 12 barres et les convertit via l'axe OCR."""
    folded_title = _fold(title)
    if (
        "volume" not in folded_title
        or not any(marker in folded_title for marker in ("par mois", "mensuel"))
    ):
        return []

    items = _ocr_positioned_items(contexts)
    if not items:
        return []
    title_items = [
        item
        for item in items
        if "volume" in _fold(str(item["text"]))
        and any(marker in _fold(str(item["text"])) for marker in ("par mois", "mensuel"))
    ]
    if not title_items:
        return []
    expanded_query_terms = _terms(build_retrieval_query(question))
    title_item = max(
        title_items,
        key=lambda item: len(expanded_query_terms & _terms(str(item["text"]))),
    )
    target_page = int(title_item["pdf_page"])
    page_items = [
        item for item in items if int(item["pdf_page"]) == target_page
    ]
    title_x = float(title_item["x"])
    title_y = float(title_item["y"])

    axis_items: list[tuple[float, float, int]] = []
    for item in page_items:
        text = str(item["text"]).replace(" ", "")
        if not re.fullmatch(r"\d{4,7}", text):
            continue
        value = int(text)
        x = float(item["x"])
        y = float(item["y"])
        if title_x - 0.03 <= x <= title_x + 0.12 and y < title_y:
            axis_items.append((x, y, value))
    if len(axis_items) < 3:
        return []
    axis_items.sort(key=lambda item: item[1])
    axis_slopes: list[float] = []
    for (_, lower_y, lower_value), (_, upper_y, upper_value) in zip(
        axis_items, axis_items[1:]
    ):
        normalized_gap = upper_y - lower_y
        value_gap = upper_value - lower_value
        if normalized_gap > 0.004 and value_gap > 0:
            axis_slopes.append(value_gap / normalized_gap)
    if len(axis_slopes) < 2:
        return []
    axis_slopes.sort()
    median_slope = axis_slopes[len(axis_slopes) // 2]
    stable_slopes = [
        slope
        for slope in axis_slopes
        if median_slope / 2.0 <= slope <= median_slope * 2.0
    ]
    if not stable_slopes:
        return []
    units_per_normalized_y = sum(stable_slopes) / len(stable_slopes)
    top_axis_y = max(item[1] for item in axis_items)

    image_paths = [
        Path(str(context.get("image_path", "")))
        for context in contexts
        if context.get("image_path")
        and int(context.get("pdf_page", -1)) == target_page
    ]
    image_path = next((path for path in image_paths if path.is_file()), None)
    if image_path is None:
        return []

    with Image.open(image_path) as source:
        image = source.convert("RGB")
        width, height = image.size
        pixels = image.load()
        left = max(0, int((title_x + 0.02) * width))
        right = min(width, int(0.95 * width))
        top = max(0, int((1.0 - top_axis_y) * height) - 12)
        lowest_axis_y = min(item[1] for item in axis_items)
        bottom_limit = min(
            height,
            int((1.0 - max(0.0, lowest_axis_y - 0.07)) * height),
        )

        def is_blue(x: int, y: int) -> bool:
            """Détecte les pixels bleus qui composent les barres de volume."""
            red, green, blue = pixels[x, y]
            return blue > 145 and blue - red > 48 and blue - green > 35

        column_counts: list[int] = []
        for x in range(left, right):
            count = sum(is_blue(x, y) for y in range(top, bottom_limit))
            column_counts.append(count)

        groups: list[tuple[int, int]] = []
        start: int | None = None
        for offset, count in enumerate(column_counts):
            x = left + offset
            if count >= 4 and start is None:
                start = x
            elif count < 4 and start is not None:
                if x - start >= 8:
                    groups.append((start, x - 1))
                start = None
        if start is not None and right - start >= 8:
            groups.append((start, right - 1))

        # La série mensuelle doit contenir exactement douze barres principales.
        if len(groups) != 12:
            return []
        bars: list[tuple[int, int]] = []
        for group_left, group_right in groups:
            ys = [
                y
                for x in range(group_left, group_right + 1)
                for y in range(top, bottom_limit)
                if is_blue(x, y)
            ]
            if not ys:
                return []
            bars.append((min(ys), max(ys)))

    baseline = max(bottom for _, bottom in bars)
    estimates = [
        max(0.0, ((baseline - bar_top) / height) * units_per_normalized_y)
        for bar_top, _ in bars
    ]
    rounded = [int(round(value / 5_000) * 5_000) for value in estimates]
    selected_language = (
        language if language in {"fr", "ar"} else response_language(question)
    )
    arabic = selected_language == "ar"
    months = (
        [
            "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
            "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
        ]
        if arabic
        else [
            "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre",
        ]
    )
    maximum_index = max(range(12), key=lambda index: estimates[index])
    largest_rise_index = max(
        range(1, 12),
        key=lambda index: estimates[index] - estimates[index - 1],
    )
    largest_fall_index = min(
        range(1, 12),
        key=lambda index: estimates[index] - estimates[index - 1],
    )

    def amount(value: int) -> str:
        """Ajoute les espaces de milliers aux volumes mensuels estimés."""
        return f"{value:,}".replace(",", " ")

    if arabic:
        operation_label = (
            "شيكاً"
            if "cheque" in folded_title
            else "تحويلاً"
            if "virement" in folded_title
            else "عملية"
        )
    else:
        operation_label = (
            "chèques"
            if "cheque" in folded_title
            else "virements"
            if "virement" in folded_title
            else "opérations"
        )

    monthly_values = ("؛ " if arabic else "; ").join(
        f"{month} ≈ {amount(value)}"
        for month, value in zip(months, rounded)
    )
    if arabic:
        return [
            "الأحجام التقريبية المقروءة من محور الرسم: " + monthly_values + ".",
            f"سُجّل أعلى حجم في {months[maximum_index]} بنحو "
            f"{amount(rounded[maximum_index])} {operation_label}.",
            f"حدثت أكبر زيادة شهرية بين {months[largest_rise_index - 1]} "
            f"و{months[largest_rise_index]}، بينما حدث أكبر تراجع بين "
            f"{months[largest_fall_index - 1]} و{months[largest_fall_index]}.",
        ]
    return [
        "Volumes approximatifs lus sur l’axe du graphique : " + monthly_values + ".",
        f"Le maximum se situe en {months[maximum_index]} autour de "
        f"{amount(rounded[maximum_index])} {operation_label}.",
        f"La hausse mensuelle la plus marquée intervient entre "
        f"{months[largest_rise_index - 1]} et {months[largest_rise_index]}, tandis que "
        f"le recul le plus net intervient entre {months[largest_fall_index - 1]} et "
        f"{months[largest_fall_index]}.",
    ]


def _currency_purchase_sales_summary(
    title: str,
    pages: set[int],
    supporting_results: Iterable[dict[str, Any]],
) -> list[str]:
    """Résume les achats et ventes EUR/USD à partir du texte de la même page."""
    folded_title = _fold(title)
    if not all(
        marker in folded_title
        for marker in ("achat", "vente", "devise", "euro", "usd")
    ):
        return []
    texts = sorted(
        (
            re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
            for item in supporting_results
            if int(item["pdf_page"]) in pages
        ),
        key=len,
        reverse=True,
    )

    purchases_usd = None
    purchases_eur = None
    sales = None
    for text in texts:
        if purchases_usd is None:
            purchases_usd = re.search(
                r"achats en USD.*?atteint\s+([\d ]+)\s+USD,\s+contre\s+"
                r"([\d ]+)\s+USD.*?hausse\s+de\s+(\d+)%",
                text,
                flags=re.IGNORECASE,
            )
        if purchases_eur is None:
            purchases_eur = re.search(
                r"achats en EUR.*?([\d,]+)\s+millions?\s+EUR,\s+contre\s+"
                r"([\d,]+)\s+millions?\s+EUR.*?progression\s+de\s+(\d+)%",
                text,
                flags=re.IGNORECASE,
            )
        if sales is None:
            sales = re.search(
                r"ventes.*?([\d,]+)\s+millions?\s+USD\s+contre\s+"
                r"([\d,]+)\s+millions?\s+USD.*?baisse\s+de\s+(\d+)%.*?"
                r"([\d,]+)\s+millions?\s+EUR\s+contre\s+"
                r"([\d,]+)\s+millions?\s+EUR.*?recul\s+de\s+(\d+)%",
                text,
                flags=re.IGNORECASE,
            )

    lines: list[str] = []
    if purchases_usd:
        current, previous, change = purchases_usd.groups()
        lines.append(
            "Achats de dollars : "
            f"{previous.strip()} USD en 2024 contre {current.strip()} USD en 2025, "
            f"soit +{change} %."
        )
    if purchases_eur:
        current, previous, change = purchases_eur.groups()
        lines.append(
            "Achats d’euros : "
            f"{previous} million EUR en 2024 contre {current} millions EUR en 2025, "
            f"soit +{change} %."
        )
    if sales:
        usd_current, usd_previous, usd_change, eur_current, eur_previous, eur_change = (
            sales.groups()
        )
        lines.extend(
            [
                "Ventes de dollars : "
                f"{usd_previous} millions USD en 2024 contre {usd_current} millions USD "
                f"en 2025, soit -{usd_change} %.",
                "Ventes d’euros : "
                f"{eur_previous} millions EUR en 2024 contre {eur_current} millions EUR "
                f"en 2025, soit -{eur_change} %.",
            ]
        )
    if len(lines) >= 4:
        lines.append(
            "L’évolution est donc opposée selon le sens de l’opération : les achats de "
            "devises augmentent fortement, tandis que les ventes diminuent en 2025."
        )
    return lines


def _statement_amount(text: str) -> int | None:
    """Lit une cellule comptable MRU et conserve le signe des parenthèses."""
    if any(marker in text for marker in ("/", "%", ",")):
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) < 6:
        return None
    value = int(digits)
    return -value if "(" in text and ")" in text else value


def _statement_row_values(
    items: list[dict[str, Any]],
    *markers: str,
    label_max_x: float = 0.30,
    value_min_x: float = 0.30,
) -> tuple[int, int] | None:
    """Associe une ligne d'état financier à ses colonnes 2025 et 2024."""
    labels = [
        item
        for item in items
        if all(marker in _fold(str(item["text"])) for marker in markers)
        and float(item["x"]) < label_max_x
    ]
    if not labels:
        return None
    label = min(labels, key=lambda item: len(str(item["text"])))
    label_y = float(label["y"])
    cells = [
        (float(item["x"]), value)
        for item in items
        if float(item["x"]) >= value_min_x
        and abs(float(item["y"]) - label_y) <= 0.009
        and (value := _statement_amount(str(item["text"]))) is not None
    ]
    cells.sort()
    if len(cells) < 2:
        return None
    return cells[0][1], cells[-1][1]


def _statement_billions(value: int) -> str:
    """Exprime un montant MRU en milliards avec une virgule française."""
    return f"{value / 1_000_000_000:.2f}".replace(".", ",")


def _statement_percent(value: float, signed: bool = False) -> str:
    """Formate un pourcentage comptable avec une décimale."""
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}{value:.1f}".replace(".", ",")


def _comprehensive_income_summary(
    title: str,
    contexts: list[dict[str, Any]],
) -> list[str]:
    """Analyse le compte de résultat et les autres éléments du résultat global."""
    if "resultat net" not in _fold(title) or "resultat global" not in _fold(title):
        return []
    items = _ocr_positioned_items(contexts)
    net_income = _statement_row_values(items, "resultat net", "exercice")
    comprehensive = _statement_row_values(items, "resultat global", "exercice")
    banking_income = _statement_row_values(
        items, "produit net bancaire", "apres", "risque"
    )
    if not net_income or not comprehensive or not banking_income:
        return []

    interest_income = _statement_row_values(items, "resultat net", "interets")
    commissions = _statement_row_values(items, "resultat net", "commissions")
    foreign_exchange = _statement_row_values(items, "resultat de change")
    personnel = _statement_row_values(items, "frais du personnel")
    other_comprehensive = _statement_row_values(
        items, "autres elements", "resultat global", "exercice"
    )

    net_rate = (net_income[0] / net_income[1] - 1.0) * 100.0
    comprehensive_rate = (comprehensive[0] / comprehensive[1] - 1.0) * 100.0
    banking_rate = (banking_income[0] / banking_income[1] - 1.0) * 100.0
    lines = [
        f"Le résultat net atteint {_statement_billions(net_income[0])} milliards MRU "
        f"en 2025, contre {_statement_billions(net_income[1])} milliards MRU en 2024, "
        f"soit une progression de {_statement_percent(net_rate, signed=True)} %.",
        f"Le résultat global progresse de {_statement_billions(comprehensive[1])} à "
        f"{_statement_billions(comprehensive[0])} milliards MRU "
        f"({_statement_percent(comprehensive_rate, signed=True)} %).",
        f"Le produit net bancaire après coût du risque augmente de "
        f"{_statement_billions(banking_income[1])} à "
        f"{_statement_billions(banking_income[0])} milliards MRU "
        f"({_statement_percent(banking_rate, signed=True)} %).",
    ]
    if interest_income and commissions and foreign_exchange:
        lines.append(
            f"La composition évolue fortement : le résultat net d’intérêts recule de "
            f"{_statement_billions(interest_income[1])} à "
            f"{_statement_billions(interest_income[0])} milliards MRU, tandis que le "
            f"résultat net des commissions monte légèrement de "
            f"{_statement_billions(commissions[1])} à "
            f"{_statement_billions(commissions[0])} milliard MRU. Le résultat de change "
            f"passe d’une perte de {_statement_billions(abs(foreign_exchange[1]))} "
            f"milliard MRU à un gain de {_statement_billions(foreign_exchange[0])} "
            "milliards MRU ; c’est le principal retournement visible."
        )
    if personnel:
        personnel_rate = (abs(personnel[0]) / abs(personnel[1]) - 1.0) * 100.0
        lines.append(
            f"Les frais du personnel augmentent en valeur absolue de "
            f"{_statement_billions(abs(personnel[1]))} à "
            f"{_statement_billions(abs(personnel[0]))} milliards MRU "
            f"({_statement_percent(personnel_rate, signed=True)} %), ce qui absorbe une "
            "partie de l’amélioration des revenus."
        )
    if other_comprehensive:
        lines.append(
            f"Les autres éléments du résultat global représentent "
            f"{_statement_billions(other_comprehensive[0])} milliard MRU en 2025 contre "
            f"{_statement_billions(other_comprehensive[1])} milliard MRU en 2024."
        )
    lines.append(
        "En synthèse, la rentabilité s’améliore nettement, portée surtout par le change "
        "et les autres activités, malgré le recul de la marge nette d’intérêts et la "
        "hausse des charges de personnel. Le tableau décrit les variations ; leurs causes "
        "doivent être confirmées dans les notes annexes."
    )
    return lines


def _cash_flow_summary(
    title: str,
    contexts: list[dict[str, Any]],
) -> list[str]:
    """Analyse les flux d'exploitation, d'investissement et de financement."""
    if "flux de tresorerie" not in _fold(title):
        return []
    items = _ocr_positioned_items(contexts)
    operating = _statement_row_values(items, "flux", "exploitation")
    investing = _statement_row_values(items, "flux", "investissement")
    financing = _statement_row_values(items, "flux", "financement")
    net_change = _statement_row_values(items, "variation nette", "tresorerie")
    opening = _statement_row_values(items, "tresorerie", "ouverture")
    closing = _statement_row_values(items, "tresorerie", "cloture")
    if not all((operating, investing, financing, net_change, opening, closing)):
        return []

    closing_change = (closing[0] / closing[1] - 1.0) * 100.0
    return [
        f"La trésorerie de clôture diminue de {_statement_billions(closing[1])} "
        f"milliards MRU en 2024 à {_statement_billions(closing[0])} milliards MRU en "
        f"2025 ({_statement_percent(closing_change)} %).",
        f"Le flux d’exploitation bascule d’un encaissement net de "
        f"{_statement_billions(operating[1])} milliards MRU en 2024 à un décaissement "
        f"net de {_statement_billions(abs(operating[0]))} milliards MRU en 2025.",
        f"L’investissement consomme {_statement_billions(abs(investing[0]))} milliard "
        f"MRU en 2025, contre {_statement_billions(abs(investing[1]))} milliard MRU en "
        "2024. Le financement apporte encore un flux positif de "
        f"{_statement_billions(financing[0])} milliard MRU, proche des "
        f"{_statement_billions(financing[1])} milliard MRU de 2024.",
        f"Au total, la variation nette est négative de "
        f"{_statement_billions(abs(net_change[0]))} milliards MRU en 2025, après une "
        f"hausse de {_statement_billions(net_change[1])} milliards MRU en 2024. La "
        f"trésorerie passe ainsi de {_statement_billions(opening[0])} milliards MRU à "
        f"l’ouverture à {_statement_billions(closing[0])} milliards MRU à la clôture.",
        "Le principal signal est donc la forte dégradation des flux d’exploitation ; "
        "le flux positif de financement ne suffit pas à compenser les sorties "
        "d’exploitation et d’investissement.",
    ]


def _equity_changes_summary(
    title: str,
    contexts: list[dict[str, Any]],
) -> list[str]:
    """Analyse le passage des capitaux propres d'ouverture à ceux de clôture."""
    if "variations des capitaux propres" not in _fold(title):
        return []
    items = _ocr_positioned_items(contexts)

    def row_amounts(*markers: str) -> list[int]:
        labels = [
            item
            for item in items
            if 0.50 <= float(item["x"]) < 0.80
            and all(
                marker
                in re.sub(r"[^a-z0-9]+", " ", _fold(str(item["text"]))).strip()
                for marker in markers
            )
        ]
        if not labels:
            return []
        label = min(labels, key=lambda item: len(str(item["text"])))
        y = float(label["y"])
        cells: list[tuple[float, int]] = []
        for item in items:
            if float(item["x"]) < 0.80 or abs(float(item["y"]) - y) > 0.009:
                continue
            parsed = _statement_amount(str(item["text"]))
            if parsed is not None:
                cells.append((float(item["x"]), parsed))
        return [value for _, value in sorted(cells)]

    opening_values = row_amounts("solde au 31 12 2024")
    closing_values = row_amounts("solde au 31 12 2025")
    net_values = row_amounts("resultat net", "2025")
    dividend_values = row_amounts("distribution de dividendes")
    correction_values = row_amounts("correction d erreur")
    if not opening_values or not closing_values or not net_values or not dividend_values:
        return []
    opening = opening_values[-1]
    closing = closing_values[-1]
    net_income = max(net_values)
    dividend = min(dividend_values)
    correction = max(correction_values) if correction_values else 0
    total_change = closing - opening
    other_comprehensive = total_change - net_income - dividend - correction
    growth = (closing / opening - 1.0) * 100.0
    return [
        f"Les capitaux propres passent de {_statement_billions(opening)} milliards MRU "
        f"au 31/12/2024 à {_statement_billions(closing)} milliards MRU au 31/12/2025, "
        f"soit +{_statement_billions(total_change)} milliards MRU "
        f"({_statement_percent(growth, signed=True)} %).",
        f"Le résultat net 2025 apporte {_statement_billions(net_income)} milliards MRU, "
        f"tandis que la distribution de dividendes retranche "
        f"{_statement_billions(abs(dividend))} milliard MRU.",
        f"Le solde des autres éléments du résultat global ressort à environ "
        f"{_statement_billions(other_comprehensive)} milliard MRU après rapprochement "
        "des mouvements visibles ; une correction d’erreur de faible montant complète "
        "le passage au solde de clôture.",
        "La progression des capitaux propres est donc principalement alimentée par le "
        "résultat net et les réévaluations, après déduction du dividende versé au Trésor.",
    ]


def _financial_position_summary(
    title: str,
    contexts: list[dict[str, Any]],
) -> list[str]:
    """Analyse les deux colonnes du bilan 2025-2024 extrait par OCR local."""
    if "situation financiere" not in _fold(title):
        return []
    items = _ocr_positioned_items(contexts)

    def amount(text: str) -> int | None:
        """Convertit une cellule MRU dont l'espacement OCR peut être irrégulier."""
        if any(marker in text for marker in ("/", "%", ",")):
            return None
        digits = re.sub(r"\D", "", text)
        return int(digits) if len(digits) >= 6 else None

    def row_values(*markers: str) -> tuple[int, int] | None:
        """Associe un libellé aux cellules 2025 et 2024 placées sur la même ligne."""
        labels = [
            item
            for item in items
            if all(marker in _fold(str(item["text"])) for marker in markers)
            and float(item["x"]) < 0.80
        ]
        if not labels:
            return None
        # Le libellé « Total des passifs » est aussi contenu dans « Total des
        # passifs et capitaux propres » : le plus court correspond à la ligne
        # exacte demandée, sans absorber le total d'équilibre situé plus bas.
        label = min(labels, key=lambda item: len(str(item["text"])))
        label_y = float(label["y"])
        cells = [
            (float(item["x"]), value)
            for item in items
            if float(item["x"]) >= 0.80
            and abs(float(item["y"]) - label_y) <= 0.009
            and (value := amount(str(item["text"]))) is not None
        ]
        cells.sort()
        if len(cells) < 2:
            return None
        # La colonne 2025 est à gauche de la colonne 2024 dans le tableau.
        return cells[0][1], cells[-1][1]

    def billions(value: int) -> str:
        """Formate un montant MRU en milliards avec deux décimales."""
        return f"{value / 1_000_000_000:.2f}".replace(".", ",")

    def rate(current: int, previous: int) -> float:
        """Calcule la variation relative entre les deux clôtures."""
        return (current / previous - 1.0) * 100.0

    def percent(value: float, signed: bool = False) -> str:
        """Formate un taux avec une décimale et une virgule française."""
        prefix = "+" if signed and value > 0 else ""
        return f"{prefix}{value:.1f}".replace(".", ",")

    total_assets = row_values("total", "actifs")
    total_liabilities = row_values("total", "passifs")
    if total_assets is None or total_liabilities is None:
        return []
    assets_25, assets_24 = total_assets
    liabilities_25, liabilities_24 = total_liabilities
    equity_25 = assets_25 - liabilities_25
    equity_24 = assets_24 - liabilities_24

    lines = [
        f"Le total des actifs atteint {billions(assets_25)} milliards MRU au "
        f"31/12/2025, contre {billions(assets_24)} milliards MRU au 31/12/2024 : "
        f"le bilan augmente de {billions(assets_25 - assets_24)} milliards MRU "
        f"({percent(rate(assets_25, assets_24), signed=True)} %).",
    ]

    treasury = row_values("tresorerie", "depots", "etrangere")
    foreign_investments = row_values("placements", "monnaie", "etrangere")
    state_receivables = row_values("creances", "etat", "amorti")
    if treasury and foreign_investments and state_receivables:
        lines.append(
            "La hausse des actifs vient surtout des placements en monnaie étrangère, "
            f"qui progressent de {billions(foreign_investments[0] - foreign_investments[1])} "
            f"milliards MRU ({percent(rate(*foreign_investments), signed=True)} %). "
            "Elle est partiellement "
            f"compensée par la baisse de la trésorerie et des dépôts en monnaie étrangère "
            f"de {billions(treasury[1] - treasury[0])} milliards MRU "
            f"({percent(rate(*treasury))} %) et par celle des créances sur l’État de "
            f"{billions(state_receivables[1] - state_receivables[0])} milliards MRU "
            f"({percent(rate(*state_receivables))} %)."
        )

    lines.append(
        f"Les passifs passent de {billions(liabilities_24)} à "
        f"{billions(liabilities_25)} milliards MRU, soit +"
        f"{billions(liabilities_25 - liabilities_24)} milliards MRU "
        f"({percent(rate(liabilities_25, liabilities_24), signed=True)} %)."
    )
    currency = row_values("billets", "circulation")
    deposits = row_values("comptes", "depots", "amorti")
    monetary = row_values("engagements", "politique", "monetaire")
    if currency and deposits and monetary:
        lines.append(
            "Dans les passifs, les billets et monnaies en circulation augmentent de "
            f"{billions(currency[0] - currency[1])} milliards MRU "
            f"({percent(rate(*currency), signed=True)} %) et les engagements liés à la politique monétaire "
            f"de {billions(monetary[0] - monetary[1])} milliards MRU "
            f"({percent(rate(*monetary), signed=True)} %). À l’inverse, les comptes courants et dépôts "
            f"reculent de {billions(deposits[1] - deposits[0])} milliards MRU "
            f"({percent(rate(*deposits))} %)."
        )

    equity_ratio_25 = equity_25 / assets_25 * 100.0
    equity_ratio_24 = equity_24 / assets_24 * 100.0
    lines.extend(
        [
            f"Les capitaux propres, obtenus par différence entre actifs et passifs et "
            f"confirmés par l’équilibre du tableau, progressent de "
            f"{billions(equity_24)} à {billions(equity_25)} milliards MRU, soit "
            f"{percent(rate(equity_25, equity_24), signed=True)} %.",
            f"Le poids des capitaux propres dans le total du bilan passe ainsi "
            f"d’environ {percent(equity_ratio_24)} % à {percent(equity_ratio_25)} %. La structure "
            "comptable se renforce donc, mais ce ratio ne doit pas être confondu avec "
            "un ratio prudentiel de solvabilité.",
            "En synthèse, le bilan s’accroît et les capitaux propres progressent plus "
            "vite que les passifs. Le tableau montre aussi une réallocation importante "
            "des actifs vers les placements en monnaie étrangère ; les causes détaillées "
            "de chaque mouvement doivent être recherchées dans les notes annexes.",
        ]
    )
    return lines


def _organization_chart_summary(
    title: str,
    contexts: list[dict[str, Any]],
) -> list[str]:
    """Restitue les niveaux visibles de l'organigramme sans inventer les missions."""
    visible_text = _fold(
        " ".join(
            line
            for context in contexts
            for line in _context_ocr_lines(context)
        )
    )
    if "organigramme" not in _fold(title) and "organigramme" not in visible_text:
        return []

    # Ces garde-fous empêchent d'utiliser ce résumé sur une autre illustration.
    required_markers = (
        "conseil general",
        "le gouverneur",
        "gouverneurs",
        "direction generale",
        "directions regionales",
    )
    if sum(marker in visible_text for marker in required_markers) < 4:
        return []

    return [
        "Au sommet figurent quatre conseils : le Conseil Général, le Conseil de "
        "Politique Monétaire, le Conseil Prudentiel, de Résolution et de Stabilité "
        "Financière, et le Conseil des Systèmes de Paiement, de Compensation et de "
        "Règlement des Titres.",
        "Le Gouverneur constitue le nœud central de l'organisation. Le schéma place "
        "à ses côtés l’UMEF et le PAFHD, puis deux Gouverneurs adjoints sous son "
        "autorité.",
        "Les organes rattachés autour du Gouverneur sont le Comité de Conformité aux "
        "Prescriptions de la Chariaa, le Comité d’Audit, le Comité des Rémunérations, "
        "le Comité de Direction, le Conseil d’Investissement, le Conseil de Veille et "
        "de Protection des Usagers du Système Financier et l’Auditeur externe.",
        "Le niveau d'appui comprend le Secrétaire Général, le Cabinet du Gouverneur, "
        "les Conseillers du Gouverneur et la Commission des Marchés, ainsi que le "
        "Caissier général, le Contrôleur Général, le Conseiller Juridique et "
        "l’Auditeur interne.",
        "L'exécution opérationnelle est répartie entre neuf directions générales : "
        "Administration ; Marchés de Capitaux ; Comptabilité ; Systèmes et Moyens de "
        "Paiement ; Balance de Paiement et Commerce Extérieur ; Supervision Bancaire "
        "et Stabilité Financière ; Études et Stabilité Monétaire ; Informatique ; "
        "Règlements. Les Directions Régionales complètent ce niveau.",
        "L'organigramme indique les rattachements, mais ne développe ni les missions "
        "de chaque entité ni la signification des sigles UMEF et PAFHD.",
    ]


def explain_chart_locally(
    question: str,
    contexts: list[dict[str, Any]],
    supporting_results: Iterable[dict[str, Any]],
    language: str | None = None,
) -> str | None:
    """Produit une lecture prudente à partir de l'OCR et du texte de la même page."""
    if not contexts:
        return None
    selected_language = (
        language if language in {"fr", "ar"} else response_language(question)
    )
    query_terms = _terms(build_retrieval_query(question))
    pages = {int(context["pdf_page"]) for context in contexts}
    all_lines: list[tuple[str, int]] = []
    for context in contexts:
        page = int(context["pdf_page"])
        all_lines.extend((line, page) for line in _context_ocr_lines(context))

    title_candidates: list[tuple[float, str, int]] = []
    for line, page in all_lines:
        folded = _fold(line)
        if not 8 <= len(line) <= 150 or "rapport annuel" in folded:
            continue
        visual_words = sum(
            marker in folded
            for marker in (
                "graphique",
                "evolution",
                "repartition",
                "structure",
                "composition",
                "par mois",
                "mensuel",
                "volume et valeur",
                "organigramme",
                "situation financiere",
            )
        )
        if not visual_words:
            continue
        score = len(query_terms & _terms(line)) * 5.0 + visual_words * 1.5
        if re.fullmatch(r"graphique\s*\d+\s*:?", folded):
            score -= 2.0
        title_candidates.append((score, line.rstrip(" :"), page))

    folded_question = _fold(question)
    statement_kind = financial_statement_kind(question)
    statement_titles = {
        "position": "État de la situation financière",
        "comprehensive_income": (
            "État du résultat net et des autres éléments du résultat global"
        ),
        "equity_changes": "État des variations des capitaux propres",
        "cash_flows": "État des flux de trésorerie",
    }
    if "organigramme" in folded_question:
        title = "Organigramme"
    elif statement_kind:
        title = statement_titles[statement_kind]
    else:
        title = "Graphique demandé"
    title_page = min(pages)
    if title_candidates and statement_kind is None:
        _, title, title_page = max(title_candidates)

    years = sorted(
        {
            int(value)
            for line, _ in all_lines
            for value in re.findall(r"\b20\d{2}\b", line)
        }
    )
    supporting = _supporting_sentences(
        question, pages, supporting_results, maximum=3
    )
    graph_numbers = sorted(
        {
            int(value)
            for line, _ in all_lines
            for value in re.findall(r"graphique\s*(\d{1,3})", _fold(line))
        }
    )
    organization_chart = "organigramme" in _fold(title)
    financial_statement = statement_kind is not None
    graph_label = f"graphique {graph_numbers[0]}" if graph_numbers else "graphique"
    if organization_chart and is_chart_existence_question(question):
        lines = [
            f"Oui. Le rapport contient l’organigramme de la BCM à la page PDF "
            f"{title_page}."
        ]
    elif organization_chart:
        lines = [
            f"L’organigramme de la BCM place le Gouverneur au centre entre les "
            f"instances de gouvernance et les structures d’exécution [p. PDF "
            f"{title_page}]."
        ]
    elif statement_kind == "position":
        lines = [
            f"L’état de la situation financière compare le bilan de la BCM au "
            f"31 décembre 2025 à celui du 31 décembre 2024 [p. PDF {title_page}]."
        ]
    elif financial_statement:
        lines = [
            f"L’« {title} » compare les montants 2025 et 2024 de la BCM "
            f"[p. PDF {title_page}]."
        ]
    elif is_chart_existence_question(question):
        lines = [
            f"Oui. Le rapport contient le {graph_label} « {title} » à la page PDF "
            f"{title_page}."
        ]
    else:
        lines = [
            f"Le {graph_label} « {title} » est analysé à partir de la page PDF "
            f"{title_page} et de ses libellés extraits localement."
        ]
    structured_summary = _financial_position_summary(title, contexts)
    if not structured_summary:
        structured_summary = _comprehensive_income_summary(title, contexts)
    if not structured_summary:
        structured_summary = _equity_changes_summary(title, contexts)
    if not structured_summary:
        structured_summary = _cash_flow_summary(title, contexts)
    if not structured_summary:
        structured_summary = _organization_chart_summary(title, contexts)
    if not structured_summary:
        structured_summary = _deposit_credit_intermediation_summary(title, contexts)
    monthly_summary: list[str] = []
    if not structured_summary:
        monthly_summary = _monthly_volume_summary(
            question, title, contexts, selected_language
        )
        structured_summary = monthly_summary
    if not structured_summary:
        structured_summary = _currency_purchase_sales_summary(
            title, pages, supporting_results
        )
    if selected_language == "ar" and monthly_summary:
        number_label = (
            f"الرسم البياني رقم {graph_numbers[0]}"
            if graph_numbers
            else "الرسم البياني"
        )
        arabic_lines = [
            f"يعرض {number_label} الحجم الشهري للتحويلات خلال عام 2025 "
            f"[p. PDF {title_page}].",
            "\n**ما الذي يوضحه الرسم؟**",
        ]
        arabic_lines.extend(
            f"- {sentence} [p. PDF {title_page}]"
            for sentence in monthly_summary
        )
        if years:
            arabic_lines.extend(
                [
                    "\n**القراءة البصرية**",
                    "- الفترات الزمنية الظاهرة: "
                    + "، ".join(str(year) for year in years)
                    + f" [p. PDF {title_page}].",
                ]
            )
        arabic_lines.append(
            "- تستند هذه الملاحظات إلى الرسم وتعليق الصفحة نفسها، ولم تُخمن "
            "أي قيمة غير مقروءة."
        )
        return "\n".join(arabic_lines)
    if structured_summary:
        lines.append(
            "\n**Structure hiérarchique**"
            if organization_chart
            else "\n**Analyse financière**"
            if financial_statement
            else "\n**Ce que montre le graphique**"
        )
        for sentence in structured_summary:
            lines.append(f"- {sentence} [p. PDF {title_page}]")
    elif supporting:
        lines.append("\n**Message principal**")
        for sentence, page in supporting:
            lines.append(f"- {sentence} [p. PDF {page}]")
    lines.append("\n**Lecture visuelle**")
    if years:
        lines.append(
            "- Repères temporels détectés : "
            + ", ".join(str(year) for year in years)
            + f" [p. PDF {title_page}]."
        )
    visual_source = (
        "de l’organigramme"
        if organization_chart
        else "du tableau"
        if financial_statement
        else "du graphique"
    )
    lines.append(
        f"- Les observations ci-dessus proviennent {visual_source} et du commentaire "
        "de la même page ; aucun libellé illisible n'a été reconstitué."
    )
    return "\n".join(lines)
