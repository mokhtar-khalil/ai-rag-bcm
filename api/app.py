"""API Flask et orchestration complète d'une question adressée au RAG BCM.

Ce module valide les requêtes, pilote la recherche documentaire, la
clarification, l'analyse locale des graphiques et la génération de la réponse.
Il expose également les routes de santé et de réindexation du rapport.
"""

from __future__ import annotations

import re
import hmac
import json
import unicodedata
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request, stream_with_context
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import BadRequest, HTTPException

from api.providers import (
    stream_answer,
    answer_with_provider,
    plan_queries_openai,
    rerank_openai,
    resolve_provider,
)
from api.embeddings import embed_documents, embed_query, warm_embedding_model
from api.charts import (
    enrich_chart_query,
    explain_chart_locally,
    explicit_chart_numbers,
    extract_document_page_contexts,
    is_chart_existence_question,
    extract_chart_contexts,
    is_chart_question,
    render_chart_pages,
    select_chart_pages,
)
from api.analytics import ensure_schema, log_question
from api.rag import RAGIndex
from api.sources import SOURCE_TYPE_PDF, citation_label
from api.query import build_retrieval_query, is_national_question
from core.config import Settings, get_settings
from core.language import (
    format_arabic_bidi,
    is_arabic_text,
    missing_information_message,
    response_language,
    untranslated_latin_words,
)
from core.logging_config import configure_logging


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _fold_text(value: str) -> str:
    """Normalise un texte pour effectuer des comparaisons sans accents ni casse."""
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _source_excerpt(text: str, question: str, limit: int = 420) -> str:
    """Choisit la fenêtre la plus utile, y compris au milieu d'un tableau."""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact

    folded_question = _fold_text(question)
    folded_compact = _fold_text(compact)
    terms = {
        term
        for term in re.findall(r"[\wÀ-ÿ]{4,}", folded_question)
        if term not in {"dans", "entre", "pour", "avec", "quel", "quelle"}
    }
    numeric_question = any(
        marker in folded_question
        for marker in ("montant", "compar", "taux", "niveau", "2024", "2025")
    )
    candidate_starts = set(range(0, len(compact) - limit + 1, max(60, limit // 3)))
    candidate_starts.add(len(compact) - limit)
    for term in terms:
        position = folded_compact.find(term)
        while position >= 0:
            candidate_starts.add(
                max(0, min(position - limit // 3, len(compact) - limit))
            )
            position = folded_compact.find(term, position + 1)

    best_start = 0
    best_score = -1.0
    for start in sorted(candidate_starts):
        window = compact[start : start + limit]
        folded_window = _fold_text(window)
        score = sum(term in folded_window for term in terms) * 2.0
        question_pairs = zip(
            re.findall(r"[a-z0-9]{4,}", folded_question),
            re.findall(r"[a-z0-9]{4,}", folded_question)[1:],
        )
        score += sum(
            f"{first} {second}" in folded_window for first, second in question_pairs
        ) * 2.5
        if numeric_question:
            score += min(len(re.findall(r"\d", window)), 30) / 10
        if score > best_score:
            best_score = score
            best_start = start
    excerpt = compact[best_start : best_start + limit].strip()
    return f"{'… ' if best_start else ''}{excerpt}{' …' if best_start + limit < len(compact) else ''}"


def _needs_query_planning(results: list[dict[str, Any]], question: str) -> bool:
    """Déclenche la reformulation seulement lorsque l'appariement initial est fragile."""
    if not results:
        return True
    best = results[0]
    lexical = float(best.get("lexical_score", best.get("score", 0.0)))
    overlap = int(best.get("keyword_overlap", 0))
    keyword_count = max(int(best.get("query_keyword_count", 0)), 1)
    coverage = overlap / keyword_count
    compact_question = len(re.findall(r"[A-Za-zÀ-ÿ0-9]{3,}", question)) <= 12
    folded_question = _fold_text(question)
    folded_best = _fold_text(str(best.get("text", "")))
    phrase_terms = [
        term
        for term in re.findall(r"[a-z0-9]{4,}", folded_question)
        if term
        not in {
            "avec", "cest", "dans", "est", "quoi", "quel", "quelle",
            "rapport", "mauritanie", "page", "tableau", "graphique",
        }
    ]
    exact_phrase = any(
        f"{first} {second}" in folded_best
        for first, second in zip(phrase_terms, phrase_terms[1:])
    )
    if exact_phrase and lexical >= 0.18:
        return False
    if is_arabic_text(question) and lexical >= 0.15 and overlap >= 2:
        # Le glossaire bilingue a déjà produit une requête française probante.
        # Un second appel de traduction serait redondant et ralentirait la réponse.
        return False
    # Une question courte n'est pas ambiguë par nature. Si son vocabulaire
    # correspond déjà fortement à un titre du rapport, une reformulation peut au
    # contraire introduire des définitions externes et dégrader la recherche.
    return (
        lexical < 0.12
        or coverage < 0.28
        or (compact_question and (lexical < 0.22 or coverage < 0.60))
    )


def _explicit_report_reference_pages(
    question: str,
    chunks: list[Any],
) -> list[int]:
    """Résout un numéro de tableau ou graphique vers sa vraie page PDF.

    Les numéros imprimés du rapport diffèrent des pages du fichier PDF à cause
    des pages liminaires. Le numéro du tableau ou du graphique constitue donc
    l'ancre stable ; les occurrences de la table des matières sont exclues.
    """
    folded_question = _fold_text(question)
    references: list[tuple[str, int]] = []
    patterns = {
        "tableau": r"(?:tableau|الجدول)(?:\s+(?:n|no|numero|رقم))?\s*[:#-]?\s*(\d{1,3})",
        "graphique": r"(?:graphique|graphe|figure|الرسم)(?:\s+(?:n|no|numero|رقم))?\s*[:#-]?\s*(\d{1,3})",
    }
    for kind, pattern in patterns.items():
        references.extend(
            (kind, int(value))
            for value in re.findall(pattern, folded_question, flags=re.IGNORECASE)
        )
    pages: list[int] = []
    for kind, number in references:
        marker = re.compile(rf"\b{kind}\s*{number}\s*:?", flags=re.IGNORECASE)
        candidates: list[tuple[int, int]] = []
        for chunk in chunks:
            page = int(chunk.pdf_page)
            if page <= 12:
                continue
            folded_chunk = _fold_text(chunk.text)
            if not marker.search(folded_chunk):
                continue
            numeric_density = min(len(re.findall(r"\d", chunk.text)), 100)
            candidates.append((numeric_density, page))
        if candidates:
            _, page = max(candidates)
            if page not in pages:
                pages.append(page)
    return pages[:3]


def _clarification_answer(
    suggestions: list[str], missing: bool = False, language: str = "fr"
) -> str:
    """Construit la réponse demandant à l'utilisateur de préciser son intention."""
    if language == "ar":
        lead = (
            "لا أجد تطابقًا دقيقًا بما يكفي للإجابة دون مخاطرة."
            if missing
            else "يمكن أن يشير سؤالك إلى عدة مؤشرات في التقرير."
        )
        lines = [f"{index}. {value}" for index, value in enumerate(suggestions, start=1)]
        answer = (
            f"{lead}\n\nيرجى تأكيد إحدى الصياغات التالية:\n\n"
            + "\n".join(lines)
        )
        return format_arabic_bidi(answer)
    lead = (
        "Je ne trouve pas de correspondance suffisamment précise pour répondre sans risque."
        if missing
        else "Votre question peut correspondre à plusieurs indicateurs du rapport."
    )
    lines = [f"{index}. {value}" for index, value in enumerate(suggestions, start=1)]
    return f"{lead}\n\nPouvez-vous confirmer l'une de ces formulations ?\n\n" + "\n".join(lines)


def _matching_terms(value: str) -> set[str]:
    """Extrait les termes métier utiles à la comparaison d'une question et d'un libellé."""
    ignored = {
        "annee", "annees", "avec", "comparaison", "comparer",
        "dans", "entre", "montant", "quelle", "quelles", "rapport", "2024", "2025",
    }
    terms: set[str] = set()
    for term in re.findall(r"[a-z0-9]{4,}", _fold_text(value)):
        if term in ignored:
            continue
        root = term.rstrip("s")
        if root.startswith(("banqu", "bancair")):
            root = "banque"
        elif root.startswith(("client", "clientel")):
            root = "client"
        terms.add(root)
    return terms


def _evidence_labels(text: str) -> list[str]:
    """Repère dans un passage les intitulés de tableaux ou d'indicateurs réels."""
    compact = " ".join(text.split())
    labels: list[str] = []
    labels.extend(
        match.strip()
        for match in re.findall(
            r"(?:poste|rubrique)\s+«([^»]{5,120})»",
            compact,
            flags=re.IGNORECASE,
        )
    )
    labels.extend(
        match.strip()
        for match in re.findall(
            r"Note\s+\d+\s*:\s*(.{8,120}?)(?=\s+(?:Ce poste|Cette rubrique|Le poste|Note\s+\d+\s*:)|$)",
            compact,
            flags=re.IGNORECASE,
        )
    )
    value_pattern = re.compile(
        r"(?<![\w])[+-]?\(?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:,\d+)?%?\)?"
    )
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        matches = list(value_pattern.finditer(line))
        if len(matches) < 2:
            continue
        label = line[: matches[0].start()].strip(" :-")
        if len(label) >= 5 and re.search(r"[A-Za-zÀ-ÿ]", label):
            labels.append(label)
    unique: list[str] = []
    seen: set[str] = set()
    for label in labels:
        cleaned = re.sub(r"\s+", " ", label).strip(" ,;:-")[:140]
        key = _fold_text(cleaned)
        if key in {
            "indicateur",
            "indicateurs",
            "valeur",
            "valeurs",
            "variation",
            "variations",
        } or key.startswith(("chiffres en ", "donnees en ", "rapport annuel")):
            continue
        if any(
            marker in key
            for marker in (
                " s'est ",
                " s’est ",
                " a atteint",
                " ont atteint",
                " s'est etabli",
                " s’est etabli",
                " en hausse",
                " en baisse",
                " represente",
            )
        ):
            continue
        if key and key not in seen:
            unique.append(cleaned)
            seen.add(key)
    return unique


def _label_is_usable(label: str) -> bool:
    """Écarte les fragments de phrase capturés à la place d'un vrai intitulé.

    L'extraction prend ce qui précède le premier nombre d'une ligne chiffrée.
    Sur une phrase ordinaire, cela produit « a traité », « Total » ou
    « » est constitué des intérêts courus du ». Proposer de tels libellés comme
    reformulation donne l'impression que l'assistant n'a rien compris.
    """
    nettoye = label.strip()
    if len(nettoye) < 8:
        return False
    # Une capture qui démarre sur une ponctuation de fermeture vient du milieu
    # d'une phrase, jamais du début d'un intitulé.
    if nettoye[0] in "»)],;:.":
        return False
    mots = re.findall(r"[A-Za-zÀ-ÿ']{2,}", nettoye)
    if len(mots) < 2:
        return False
    # Un intitulé commence par un nom, pas par un verbe conjugué ni une
    # conjonction ramassée en cours de phrase.
    premier = _fold_text(mots[0])
    if premier in {
        # Verbes conjugués : la capture a démarré au milieu d'une phrase.
        "a", "ont", "est", "sont", "etait", "etaient", "sera", "seront",
        # Conjonctions et pronoms relatifs.
        "et", "ou", "mais", "donc", "car", "dont", "que", "qui",
        # Prépositions : un intitulé d'indicateur commence par un nom, jamais
        # par « Pour les normes IFRS » ou « Selon la méthode retenue ».
        "a", "au", "aux", "dans", "de", "des", "du", "en", "par", "pour",
        "selon", "sans", "sous", "sur", "vers", "depuis", "apres", "avant",
        "entre", "parmi", "chez", "avec", "malgre", "durant", "pendant",
        # Déterminants et pronoms démonstratifs.
        "soit", "ainsi", "cette", "ces", "ce", "cet", "il", "elle", "on",
        "leur", "leurs", "son", "sa", "ses", "notre", "nos",
    }:
        return False
    return True


def _evidence_suggestions(
    question: str, results: list[dict[str, Any]], maximum: int = 2
) -> list[str]:
    """Propose uniquement des périmètres réellement présents dans les passages locaux."""
    query_terms = _matching_terms(question)
    folded_question = _fold_text(question)
    years = sorted({int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", question)})
    candidates: list[tuple[int, float, str, set[str]]] = []
    seen_pages: set[int] = set()
    for rank, item in enumerate(results):
        page = int(item["pdf_page"])
        if page in seen_pages:
            continue
        best_label: str | None = None
        best_terms: set[str] = set()
        best_overlap = 0
        for label in _evidence_labels(item["text"]):
            if not _label_is_usable(label):
                continue
            # Le recouvrement se mesure sur le libellé tel qu'il figure dans le
            # document. Le mesurer après ajout du préfixe rendait éligible
            # n'importe quel fragment : « étendue de » devenait
            # « Dépôts — étendue de » et paraissait répondre à la question.
            context = _fold_text(item["text"])
            if (
                "depot" in folded_question
                and "depot" in context
                and "depot" not in _fold_text(label)
            ):
                label = f"Dépôts — {label}"
            if "secteur bancaire" in context and "bancair" not in _fold_text(label):
                label = f"{label} — secteur bancaire"
            label_terms = _matching_terms(label)
            overlap = len(query_terms & label_terms)
            if overlap > best_overlap:
                best_label = label
                best_terms = label_terms
                best_overlap = overlap
        # Un seul mot commun ne justifie pas de proposer une reformulation : le
        # mot « dépôt » d'une question sur les wallets faisait remonter des
        # lignes comptables du Trésor, sans rapport avec la demande. Une vraie
        # alternative partage au moins deux termes avec la question.
        if not best_label or best_overlap < 2:
            continue
        seen_pages.add(page)
        candidates.append(
            (best_overlap, float(item["score"]) - rank * 0.002, best_label, best_terms)
        )

    candidates.sort(key=lambda value: (value[0], value[1]), reverse=True)
    selected: list[tuple[str, set[str]]] = []
    for _, _, label, terms in candidates:
        if any(
            (
                len(terms & previous_terms) / max(len(terms | previous_terms), 1) > 0.65
                or len(terms & previous_terms) / max(min(len(terms), len(previous_terms)), 1)
                > 0.65
            )
            for _, previous_terms in selected
        ):
            continue
        selected.append((label, terms))
        if len(selected) >= maximum:
            break

    suggestions: list[str] = []
    for label, _ in selected:
        if len(years) == 2:
            suggestions.append(
                f"Comparer l'indicateur « {label} » entre {years[0]} et {years[1]}"
            )
        else:
            suggestions.append(f"Que dit le rapport sur l'indicateur « {label} » ?")
    return suggestions


def _safe_history(value: Any, maximum: int) -> list[dict[str, str]]:
    """Valide, tronque et borne l'historique transmis par le frontend."""
    if not isinstance(value, list) or maximum == 0:
        return []
    history: list[dict[str, str]] = []
    for item in value[-maximum:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        history.append({"role": role, "content": content[:4000]})
    return history


def _answer_without_source_block(answer: str) -> str:
    """Retire le bloc d'affichage des sources ajouté par le frontend Gradio."""
    value = re.split(
        r"\n\n---\n\*\*(?:Sources consultées|المصادر المستعان بها)\*\*",
        answer,
        maxsplit=1,
    )[0]
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _followup_markers(question: str) -> bool:
    """Détecte une demande qui dépend explicitement d'un échange antérieur."""
    folded = _fold_text(question)
    return any(
        marker in folded
        for marker in (
            "cette section",
            "ce chapitre",
            "ce passage",
            "ce sujet",
            "ce point",
            "ce rapport",
            "ce tableau",
            "ce graphique",
            "cette partie",
            "cette reponse",
            "cela",
            "celui ci",
            "celle ci",
            "le meme sujet",
            "la meme chose",
            "plus de details",
            "approfond",
            "developpe",
            "explique mieux",
            "son resume",
            "le resume",
            "resume le",
            "resume cette",
            "repete",
            "rappelle",
            "redonne",
            "tu disais",
            "tu as dit",
            "qu as tu dit",
            "qu avais tu dit",
            "reponse precedente",
            "dans la page",
            "a la page",
            "هذا القسم",
            "هذه الفقرة",
            "هذا الفصل",
            "هذا الموضوع",
            "هذا الجزء",
            "هذا الرسم",
            "هذا الجدول",
            "ملخص",
            "لخص",
            "اشرح اكثر",
            "اشرح بالتفصيل",
            "كرر",
            "اعد ما قلت",
            "أعد ما قلت",
            "ماذا قلت",
            "في الصفحة",
        )
    )


def _history_turns(history: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Regroupe l'historique plat en couples question-réponse exploitables."""
    turns: list[tuple[str, str]] = []
    pending_user = ""
    for item in history:
        if item["role"] == "user":
            pending_user = item["content"]
        elif pending_user:
            turns.append((pending_user, _answer_without_source_block(item["content"])))
            pending_user = ""
    if pending_user:
        turns.append((pending_user, ""))
    return turns


def _followup_focus(
    question: str,
    history: list[dict[str, str]],
) -> tuple[str, str]:
    """Choisit le tour visé, le dernier par défaut ou celui qui correspond au sujet nommé."""
    turns = _history_turns(history)
    if not turns:
        return "", ""
    ignored = {
        "alors", "cette", "chapitre", "disais", "explique", "partie",
        "passage", "point", "precedente", "rappelle", "reponse", "repete",
        "resume", "section", "sujet", "tableau", "graphique", "details",
        "هذا", "هذه", "القسم", "الفقرة", "الفصل", "الموضوع", "الجزء",
        "الرسم", "الجدول", "ملخص", "لخص", "اشرح", "كرر", "اعد", "ماذا", "قلت",
    }
    query_terms = {
        term
        for term in re.findall(r"[^\W_]{3,}", _fold_text(question), flags=re.UNICODE)
        if term not in ignored
    }
    if not query_terms:
        return turns[-1]
    scored = []
    for index, (user_text, answer_text) in enumerate(turns):
        turn_terms = set(
            re.findall(
                r"[^\W_]{3,}",
                _fold_text(f"{user_text} {answer_text}"),
                flags=re.UNICODE,
            )
        )
        scored.append((len(query_terms & turn_terms), index, user_text, answer_text))
    overlap, _, user_text, answer_text = max(scored)
    return (user_text, answer_text) if overlap else turns[-1]


def _history_pages(answer: str, question: str = "") -> list[int]:
    """Récupère d'abord les pages de la phrase qui correspond au référent demandé."""
    # Protéger l'abréviation « p. PDF » : le point ne termine pas la phrase et
    # ne doit pas séparer le mot « section » de sa citation.
    protected_answer = re.sub(r"p\.\s*PDF", "p PDF", answer, flags=re.I)
    sentences = re.split(r"(?<=[.!?])\s+|\n+", protected_answer)
    folded_question = _fold_text(question)
    target_markers = [
        marker
        for marker in (
            "section", "chapitre", "passage", "tableau", "graphique",
            "القسم", "الفصل", "الفقرة", "الجدول", "الرسم",
        )
        if marker in folded_question
    ]
    prioritized = [
        sentence
        for sentence in sentences
        if any(marker in _fold_text(sentence) for marker in target_markers)
        and re.search(r"(?:p\.?\s*PDF|page\s+PDF)\s*\d+", sentence, re.I)
    ]
    candidates = prioritized or sentences
    pages: list[int] = []
    for sentence in candidates:
        for value in re.findall(
            r"(?:p\.?\s*PDF|page\s+PDF)\s*(\d+)", sentence, re.I
        ):
            page = int(value)
            if page not in pages:
                pages.append(page)
    return pages[:3]


def _repeat_followup(question: str) -> bool:
    """Détecte une demande de répétition exacte, sans reformulation demandée."""
    folded = _fold_text(question)
    repeat = any(
        marker in folded
        for marker in (
            "repete",
            "rappelle",
            "redonne",
            "tu disais",
            "tu as dit",
            "qu as tu dit",
            "qu avais tu dit",
            "كرر",
            "اعد ما قلت",
            "أعد ما قلت",
            "ماذا قلت",
        )
    )
    transform = any(
        marker in folded
        for marker in ("resume", "reformule", "autrement", "plus simple", "explique")
    )
    return repeat and not transform


def _content_followup(question: str) -> bool:
    """Repère un suivi qui demande le contenu, le résumé ou l'analyse du sujet actif."""
    folded = _fold_text(question)
    return any(
        marker in folded
        for marker in (
            "resume",
            "synthese",
            "essentiel",
            "contenu",
            "que dit",
            "que contient",
            "plus de details",
            "approfond",
            "developpe",
            "explique",
            "analyse",
            "ملخص",
            "لخص",
            "الخلاصة",
            "المحتوى",
            "تفاصيل",
            "اشرح",
            "حلل",
        )
    )


def _contextualize_followup(
    question: str,
    history: list[dict[str, str]],
) -> str:
    """Transforme une question de suivi en demande documentaire autonome."""
    if not _followup_markers(question) or not history:
        return question
    previous_user, previous_answer = _followup_focus(question, history)
    previous_user = re.sub(r"\s+", " ", previous_user).strip()[:600]
    previous_answer = previous_answer[:1800]
    if not previous_user and not previous_answer:
        return question
    quoted_titles = re.findall(r"«([^»]{5,160})»", previous_answer)
    subject = quoted_titles[0].strip() if quoted_titles else previous_user
    pages = _history_pages(previous_answer, question)
    page_hint = f" Page(s) déjà citée(s) : {', '.join(map(str, pages))}." if pages else ""
    return f"{question}. Sujet actif : {subject}.{page_hint}"


def _report_units(
    pages: list[int], registry: dict[int, dict[str, Any]]
) -> list[int]:
    """Ne garde que les unités qui désignent réellement une page du rapport PDF.

    Le rendu d'image et l'OCR documentaire ouvrent le rapport annuel à un numéro
    de page. Une unité venant d'une Lettre d'information n'y correspond à rien :
    la laisser passer déclencherait un rendu voué à l'échec.
    """
    if not registry:
        return pages
    return [
        page
        for page in pages
        if registry.get(page, {}).get("source_type", SOURCE_TYPE_PDF) == SOURCE_TYPE_PDF
    ]


def _select_results(
    search_results: list[dict[str, Any]],
    wanted: int,
    registry: dict[int, dict[str, Any]],
    primary_type: str = SOURCE_TYPE_PDF,
) -> list[dict[str, Any]]:
    """Sélectionne les passages sans qu'une source en évince silencieusement une autre.

    Le corpus réunit le rapport annuel et les Lettres d'information. À nombre de
    passages constant, une lettre bien classée prend la place d'une page du
    rapport, qui n'atteint alors jamais la génération. La sélection est donc
    élargie jusqu'à ce que la source de référence dispose du quota qu'elle aurait
    obtenu seule, sans pour autant écarter les passages des autres sources.
    """
    if not registry:
        return search_results[:wanted]
    selected: list[dict[str, Any]] = []
    primary = 0
    for item in search_results:
        entry = registry.get(int(item["pdf_page"]), {})
        # Une unité absente du registre provient d'un index antérieur : la
        # traiter comme la source de référence conserve l'ancien comportement.
        if entry.get("source_type", primary_type) == primary_type:
            primary += 1
        selected.append(item)
        if primary >= wanted or len(selected) >= wanted * 2:
            break
    return selected


def create_app(
    settings_override: Settings | None = None,
    engine_override: RAGIndex | None = None,
) -> Flask:
    """Crée l'API ; ses réglages et son index peuvent être injectés pour les tests."""
    settings = settings_override or get_settings()
    engine = engine_override or RAGIndex(settings.report_path, settings.index_path)
    engine_lock = Lock()

    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.config.update(
        MAX_CONTENT_LENGTH=settings.max_request_bytes,
        PROPAGATE_EXCEPTIONS=False,
        TESTING=settings.is_testing,
    )
    # Une génération qui échoue est rattrapée par le repli extractif : le
    # service continue de répondre, mais avec des extraits bruts au lieu d'une
    # réponse rédigée. Sans trace exposée, la panne reste invisible — /health
    # répond « ok » et le widget affiche un point vert. Cet état la rend
    # observable sans appeler le fournisseur à chaque sonde.
    generation_state: dict[str, Any] = {"succes": 0, "echecs": 0, "dernier_echec": None}

    app.extensions["bcm_generation_state"] = generation_state
    app.extensions["bcm_settings"] = settings

    # Anti-abus par session : nombre de questions posées depuis la dernière
    # activité, réinitialisé après SESSION_IDLE_MINUTES d'inactivité. Vit en
    # mémoire, comme le compteur de RATE_LIMIT_ASK : exact avec un seul worker
    # (WEB_CONCURRENCY=1, requis pour la même raison — voir DEPLOIEMENT), mais
    # se dédoublerait avec plusieurs workers.
    session_lock = Lock()
    session_state: dict[str, dict[str, float]] = {}
    SESSION_STATE_MAX = 5000

    def _check_session_limit(session_id: str) -> tuple[bool, int]:
        """Incrémente le compteur de la session et dit si la limite est dépassée."""
        now = perf_counter()
        idle_seconds = settings.session_idle_minutes * 60
        with session_lock:
            entree = session_state.get(session_id)
            if entree is None or (now - entree["vu_a"]) > idle_seconds:
                entree = {"compte": 0.0, "vu_a": now}
            entree["compte"] += 1
            entree["vu_a"] = now
            session_state[session_id] = entree
            if len(session_state) > SESSION_STATE_MAX:
                # Éviction simple des entrées les plus anciennement actives :
                # borne la mémoire sans dépendre d'un nettoyage périodique.
                plus_ancienne = min(session_state, key=lambda k: session_state[k]["vu_a"])
                if plus_ancienne != session_id:
                    del session_state[plus_ancienne]
            compte = int(entree["compte"])
        return compte <= settings.session_max_questions, compte
    app.extensions["bcm_engine"] = engine
    configure_logging(app, settings)

    if settings.cors_allowed_origins:
        CORS(
            app,
            resources={
                r"/api/*": {"origins": list(settings.cors_allowed_origins)},
                r"/health": {"origins": list(settings.cors_allowed_origins)},
            },
        )

    # Le modèle d'embedding met environ neuf secondes à se charger. Sans
    # préchargement, cette attente est payée par la première question de chaque
    # worker : /health ne réchauffe que celui qui répond à la sonde. Le coût est
    # déplacé au démarrage, où il est invisible pour l'utilisateur.
    if settings.semantic_retrieval:
        try:
            warm_embedding_model(settings.embedding_model, settings.embedding_cache_path)
        except Exception as exc:  # le service reste utilisable en recherche lexicale
            app.logger.warning(
                "embedding_warmup_failed error_type=%s", type(exc).__name__
            )

    # La table de journalisation est créée au démarrage plutôt qu'à la première
    # question consentie : une base indisponible se signale une fois dans les
    # journaux du déploiement, pas en silence à chaque tentative d'écriture.
    try:
        ensure_schema(settings)
    except Exception as exc:  # le service reste utilisable sans journalisation
        app.logger.warning("analytics_schema_failed error_type=%s", type(exc).__name__)

    app.config["RATELIMIT_ENABLED"] = not settings.is_testing
    limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")
    app.extensions["bcm_limiter"] = limiter

    def ensure_loaded() -> None:
        """Charge l'index une seule fois, même en présence de requêtes concurrentes."""
        if not engine.chunks:
            with engine_lock:
                if not engine.chunks:
                    engine.load()

    def error_payload(
        message: str, status: int, reason: str | None = None
    ) -> tuple[Any, int]:
        """Uniformise les erreurs JSON et y joint l'identifiant de la requête."""
        corps = {"error": message, "request_id": g.get("request_id")}
        if reason:
            corps["reason"] = reason
        return jsonify(corps), status

    @app.before_request
    def start_request() -> None:
        """Initialise la traçabilité et le chronométrage de chaque appel HTTP."""
        supplied = request.headers.get("X-Request-ID", "")
        g.request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid4().hex
        g.started_at = perf_counter()

    @app.after_request
    def finish_request(response: Any) -> Any:
        """Journalise la durée de l'appel et retourne son identifiant au client."""
        request_id = g.get("request_id", uuid4().hex)
        duration_ms = (perf_counter() - g.get("started_at", perf_counter())) * 1000
        response.headers["X-Request-ID"] = request_id
        app.logger.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.path,
            response.status_code,
            duration_ms,
        )
        return response

    @app.get("/")
    def root() -> Any:
        """Décrit brièvement le service et ses principaux points d'entrée."""
        return jsonify(
            {
                "service": "BCM RAG API",
                "status": "ok",
                "environment": settings.app_env,
                "endpoints": ["GET /health", "POST /api/ask", "POST /api/reindex"],
            }
        )

    @app.get("/health")
    def health() -> Any:
        """Vérifie que l'index et, si nécessaire, le modèle sémantique sont prêts."""
        ensure_loaded()
        if settings.semantic_retrieval and engine.has_semantic_index:
            warm_embedding_model(settings.embedding_model, settings.embedding_cache_path)
        # Sélection explicite : « **engine.metadata » publiait les empreintes
        # SHA-256 de chaque document du corpus, sans utilité pour un appelant et
        # sans raison d'être exposées sur un point d'entrée public.
        return jsonify(
            {
                "status": "ok",
                "chart_analysis_enabled": settings.chart_analysis_enabled,
                "documents": engine.metadata.get("documents", 0),
                "pages_par_source": engine.metadata.get("pages_par_source", {}),
                "pdf_pages": engine.metadata.get("pdf_pages", 0),
                "chunks": engine.metadata.get("chunks", 0),
                "semantic_index": engine.metadata.get("semantic_index", False),
                "index_schema_version": engine.metadata.get("index_schema_version"),
                # Le nom du fournisseur reste privé ; seul son état est publié.
                "generation": {
                    "reponses_redigees": generation_state["succes"],
                    "echecs": generation_state["echecs"],
                    "dernier_echec": generation_state["dernier_echec"],
                    # Vrai dès qu'une génération a échoué sans succès depuis :
                    # le service répond alors par extraits bruts.
                    "degradee": generation_state["dernier_echec"] is not None,
                },
            }
        )

    def _evenement_reponse(payload: dict[str, Any]) -> tuple[str, Any]:
        """Événement final : la réponse complète, prête à être sérialisée."""
        return ("done", payload)

    def _evenement_erreur(
        message: str, status: int, reason: str | None = None
    ) -> tuple[str, Any]:
        """Événement d'erreur, porteur du corps JSON, du code HTTP et de sa cause."""
        return ("error", (message, status, reason))

    def _ask_events() -> Any:
        """Exécute toute la chaîne RAG en produisant des événements successifs.

        Le découpage en événements permet à deux points d'entrée de partager
        exactement la même logique : « /api/ask » attend l'événement final et
        renvoie un JSON unique, « /api/ask/stream » les retransmet au fil de
        l'eau. Sans ce partage, la voie diffusée aurait sa propre copie du
        pipeline et les deux divergeraient à la première correction.
        """
        ensure_loaded()
        payload = request.get_json(silent=False)
        if not isinstance(payload, dict):
            yield _evenement_erreur("Le corps JSON doit être un objet.", 400)
            return

        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            yield _evenement_erreur("Le champ 'question' est obligatoire.", 400)
            return
        question = question.strip()
        requested_language = payload.get("language")
        if requested_language is None:
            # Compatibilité avec les anciens clients : la détection ne sert que
            # lorsque l'interface n'a pas transmis son choix explicite.
            language = response_language(question)
        elif requested_language not in {"fr", "ar"}:
            yield _evenement_erreur("Le champ 'language' doit valoir 'fr' ou 'ar'.", 400)
            return
        else:
            language = requested_language
        if len(question) > settings.max_question_chars:
            yield _evenement_erreur(
                f"La question dépasse {settings.max_question_chars:,} caractères.".replace(",", " "),
                400,
            )
            return

        # Un identifiant de session absent signifie un client antérieur à cette
        # fonctionnalité : ne pas le pénaliser en lui appliquant une limite
        # qu'il ne peut pas afficher ni réinitialiser correctement.
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id.strip():
            autorise, compte = _check_session_limit(session_id.strip()[:128])
            if not autorise:
                minutes = settings.session_idle_minutes
                message = (
                    f"لقد بلغت الحد الأقصى لعدد الأسئلة لهذه الجلسة "
                    f"({settings.session_max_questions}). ستُتاح جلسة جديدة "
                    f"تلقائياً بعد {minutes} دقيقة من عدم النشاط."
                    if language == "ar"
                    else (
                        f"Vous avez atteint la limite de {settings.session_max_questions} "
                        f"questions pour cette session. Une nouvelle session démarre "
                        f"automatiquement après {minutes} minutes d'inactivité."
                    )
                )
                yield _evenement_erreur(message, 429, reason="session_limit")
                return

        history = _safe_history(payload.get("history", []), settings.max_history_messages)
        retrieval_question = _contextualize_followup(question, history)
        followup = retrieval_question != question
        focus_user, focus_answer = _followup_focus(question, history) if followup else ("", "")
        followup_pages = _history_pages(focus_answer, question) if focus_answer else []
        explicit_reference_pages = _explicit_report_reference_pages(
            question, engine.chunks
        )

        # Une demande de répétition exacte relève de la mémoire conversationnelle,
        # pas d'une nouvelle génération susceptible de modifier la réponse.
        if (
            followup
            and _repeat_followup(question)
            and focus_answer
            and language == response_language(focus_answer)
        ):
            repeated_sources: list[dict[str, Any]] = []
            for page in followup_pages:
                chunk = next(
                    (item for item in engine.chunks if int(item.pdf_page) == page),
                    None,
                )
                if chunk is not None:
                    repeated_sources.append(
                        {
                            "pdf_page": page,
                            "score": 1.0,
                            "excerpt": _source_excerpt(chunk.text, focus_user),
                        }
                    )
            yield _evenement_reponse(
                {
                    "answer": focus_answer,
                    "sources": repeated_sources,
                    "grounded": bool(followup_pages),
                    "clarification_needed": False,
                    "suggestions": [],
                    "chart_analysis": False,
                    "chart_pages": [],
                    "memory_used": True,
                    "language": language,
                }
            )
            return
        previous_users = [item["content"] for item in history[-4:] if item["role"] == "user"]
        if retrieval_question == question and len(question.split()) <= 8 and previous_users:
            retrieval_question = f"{previous_users[-1]} {question}"
        chart_requested = is_chart_question(retrieval_question)
        broad_question = chart_requested or any(
            marker in question.casefold()
            for marker in (
                "quelles", "quels", "réformes", "mesures", "initiatives",
                "الإصلاحات", "اصلاحات", "المبادرات", "التدابير",
            )
        )
        comparative_question = (
            len(set(re.findall(r"\b(?:19|20)\d{2}\b", question))) >= 2
        )
        # Le corpus réunit plusieurs sources et le TF-IDF est global : chaque
        # ajout de documents redistribue légèrement les scores des passages
        # existants. Une question large réclame donc une tranche de preuves
        # assez profonde pour absorber ce reclassement sans perdre une page.
        wanted_results = (
            max(settings.top_k, 10)
            if broad_question or comparative_question
            else settings.top_k
        )
        provider = resolve_provider(settings)
        retrieval_limit = (
            max(settings.retrieval_candidates, 30)
            if chart_requested
            else settings.retrieval_candidates
        )

        def retrieve_query(retrieval_query: str) -> list[dict[str, Any]]:
            """Exécute une recherche hybride, avec repli lexical en cas d'échec."""
            question_embedding = None
            if settings.semantic_retrieval and engine.has_semantic_index:
                try:
                    question_embedding = embed_query(
                        retrieval_query,
                        model=settings.embedding_model,
                        cache_path=settings.embedding_cache_path,
                    )
                except Exception as exc:
                    app.logger.warning(
                        "request_id=%s query_embedding_failed error_type=%s",
                        g.request_id,
                        type(exc).__name__,
                    )
            return engine.retrieve(
                retrieval_query,
                top_k=retrieval_limit,
                query_embedding=question_embedding,
                semantic_weight=settings.semantic_weight,
            )

        yield ("stage", "recherche")
        # Étape 1 : enrichir la demande et lancer une première recherche hybride.
        base_retrieval_query = build_retrieval_query(
            enrich_chart_query(retrieval_question)
        )
        retrieval_queries = [base_retrieval_query]
        result_sets = [retrieve_query(base_retrieval_query)]
        strong_arabic_retrieval = bool(
            language == "ar"
            and result_sets[0]
            and float(
                result_sets[0][0].get(
                    "lexical_score", result_sets[0][0].get("score", 0.0)
                )
            )
            >= 0.15
            and int(result_sets[0][0].get("keyword_overlap", 0)) >= 2
        )
        query_plan: dict[str, Any] = {
            "queries": [retrieval_question],
            "ambiguous": False,
            "suggestions": [],
        }
        confirmed_scope = bool(
            re.search(
                r"(?:l'indicateur|l’indicateur|la rubrique|le poste)\s+«[^»]+»",
                question,
                flags=re.IGNORECASE,
            )
        ) or bool(explicit_reference_pages)
        # Étape 2 : si les premiers résultats sont fragiles, le planificateur crée
        # plusieurs reformulations. Chaque variante est recherchée séparément.
        if (
            provider == "openai"
            and not confirmed_scope
            and not followup
            and _needs_query_planning(result_sets[0], retrieval_question)
        ):
            yield ("stage", "reformulation")
            try:
                query_plan = plan_queries_openai(retrieval_question, settings)
                known_queries = {base_retrieval_query.casefold()}
                for planned_question in query_plan["queries"]:
                    planned_query = build_retrieval_query(
                        enrich_chart_query(planned_question)
                        if chart_requested
                        else planned_question
                    )
                    key = planned_query.casefold()
                    if key in known_queries:
                        continue
                    known_queries.add(key)
                    retrieval_queries.append(planned_query)
                    result_sets.append(retrieve_query(planned_query))
            except Exception as exc:
                app.logger.warning(
                    "request_id=%s query_planning_failed error_type=%s",
                    g.request_id,
                    type(exc).__name__,
                )

        if is_national_question(question):
            result_sets = [
                [
                    item
                    for item in results
                    if not 13 <= int(item["pdf_page"]) <= 20
                ]
                for results in result_sets
            ]

        # Étape 3 : fusionner les classements des reformulations, puis appliquer
        # éventuellement un reranking plus précis sur la liste consolidée.
        search_results = engine.fuse_ranked_results(
            result_sets,
            max_results=retrieval_limit,
        )

        # Les pages déjà citées et les références explicites (« tableau 5 »)
        # deviennent prioritaires. Le numéro imprimé donné par l'utilisateur ne
        # doit jamais être confondu avec le numéro technique de la page PDF.
        priority_pages = list(dict.fromkeys([*explicit_reference_pages, *followup_pages]))
        if priority_pages:
            pinned: list[dict[str, Any]] = []
            query_terms = _matching_terms(retrieval_question)
            reference_numbers = re.findall(
                r"(?:tableau|graphique|graphe|figure)\s*(\d{1,3})",
                _fold_text(question),
            )
            for priority_page in priority_pages:
                page_chunks = [
                    chunk
                    for chunk in engine.chunks
                    if int(chunk.pdf_page) == priority_page
                ]

                def priority_score(chunk: Any) -> tuple[float, int]:
                    """Classe d'abord le titre demandé, puis ses lignes chiffrées."""
                    folded_chunk = _fold_text(chunk.text)
                    overlap = len(query_terms & _matching_terms(chunk.text))
                    exact_reference = any(
                        re.search(
                            rf"(?:tableau|graphique|graphe|figure)\s*{number}\s*:?",
                            folded_chunk,
                        )
                        for number in reference_numbers
                    )
                    numeric_density = min(len(re.findall(r"\d", chunk.text)), 80)
                    score = overlap * 8.0 + numeric_density / 12.0
                    score += 40.0 if exact_reference else 0.0
                    score += 0.5 if chunk.kind == "table_row" else 0.0
                    return score, -int(chunk.chunk_id)

                for chunk in sorted(
                    page_chunks, key=priority_score, reverse=True
                )[:6]:
                    pinned.append(
                        {
                            "chunk_id": chunk.chunk_id,
                            "pdf_page": chunk.pdf_page,
                            "text": chunk.text,
                            "score": 1.0,
                            "lexical_score": 1.0,
                            "semantic_score": None,
                            "retrieval_mode": (
                                "explicit_report_reference"
                                if priority_page in explicit_reference_pages
                                else "conversation_memory"
                            ),
                            "kind": chunk.kind,
                            "keyword_overlap": len(
                                query_terms & _matching_terms(chunk.text)
                            ),
                            "query_keyword_count": max(len(query_terms), 1),
                            "memory_page": priority_page in followup_pages,
                            "explicit_reference": (
                                priority_page in explicit_reference_pages
                            ),
                        }
                    )
                if len(pinned) >= 6:
                    break
            pinned_ids = {int(item["chunk_id"]) for item in pinned}
            search_results = [
                *pinned,
                *[
                    item
                    for item in search_results
                    if int(item["chunk_id"]) not in pinned_ids
                ],
            ]

        planned_rephrasings = [
            value
            for value in query_plan.get("queries", [])[1:]
            if isinstance(value, str)
        ][:3]
        evidence_suggestions = (
            _evidence_suggestions(question, search_results)
            if query_plan.get("ambiguous")
            else []
        )
        planner_suggestions = [
            value
            for value in query_plan.get("suggestions", [])
            if isinstance(value, str)
        ][:3]
        suggestions = (
            planner_suggestions
            if language == "ar" and planner_suggestions
            else evidence_suggestions or planned_rephrasings
        )
        # Note : une clarification est parfois demandée alors que la recherche a
        # déjà trouvé la réponse (« quelle application bancaire… » renvoie vers
        # des indicateurs de dépôts). Aucun seuil ne distingue ce faux positif
        # d'une ambiguïté réelle : les deux cas mesurés donnent des scores
        # quasi identiques (0,455 contre 0,486 ; sémantique 0,875 contre 0,878).
        # Trancher demande d'abord d'étendre le banc d'évaluation à ce
        # comportement, qu'il ne couvre pas aujourd'hui.
        clarification_needed = (
            not chart_requested
            and not followup
            and bool(query_plan.get("ambiguous"))
            and len(evidence_suggestions) >= 2
        )
        results = engine.decorate(
            _select_results(search_results, wanted_results, engine.source_registry),
            language,
        )
        if (
            not chart_requested
            and not clarification_needed
            and provider == "openai"
            and not strong_arabic_retrieval
            and engine.is_relevant(
                search_results,
                settings.min_relevance_score,
                settings.min_semantic_score,
            )
        ):
            yield ("stage", "selection")
            try:
                results = rerank_openai(retrieval_question, search_results, settings)
            except Exception as exc:
                app.logger.warning(
                    "request_id=%s reranking_failed error_type=%s",
                    g.request_id,
                    type(exc).__name__,
                )
                results = engine.decorate(
            _select_results(search_results, wanted_results, engine.source_registry),
            language,
        )

        if clarification_needed:
            results = search_results[: min(8, len(search_results))]

        # Étape 4 : pour une question graphique, rendre les pages PDF en images,
        # extraire leurs libellés par OCR et conserver les graphiques pertinents.
        chart_pages: list[int] = []
        chart_contexts: list[dict[str, Any]] = []
        if chart_requested and settings.chart_analysis_enabled:
            try:
                chart_analysis_question = enrich_chart_query(question)
                chart_pages = _report_units(
                    select_chart_pages(
                        chart_analysis_question,
                        search_results,
                        maximum=settings.chart_max_pages,
                    ),
                    engine.source_registry,
                )
                if chart_pages:
                    rendered_pages = render_chart_pages(
                        settings.report_path,
                        chart_pages,
                        settings.chart_cache_path,
                        dpi=settings.chart_render_dpi,
                        renderer_path=settings.pdf_renderer_path,
                    )
                    chart_contexts = extract_chart_contexts(
                        chart_analysis_question,
                        rendered_pages,
                        script_path=(
                            Path(__file__).resolve().parents[1]
                            / "scripts"
                            / "chart_ocr.swift"
                        ),
                        ocr_path=settings.chart_ocr_path,
                        maximum_chars=settings.chart_ocr_max_chars,
                    )
                    if chart_contexts:
                        requested_numbers = explicit_chart_numbers(question)
                        if requested_numbers:
                            chart_contexts = [
                                context
                                for context in chart_contexts
                                if any(
                                    re.search(
                                        rf"(?:graphique|graphe|figure)\s*{number}\b",
                                        _fold_text(str(context.get("text", ""))),
                                    )
                                    for number in requested_numbers
                                )
                            ]
                        else:
                            best_overlap = max(
                                int(context.get("keyword_overlap", 0))
                                for context in chart_contexts
                            )
                            minimum_overlap = max(1, best_overlap - 1)
                            chart_contexts = [
                                context
                                for context in chart_contexts
                                if int(context.get("keyword_overlap", 0))
                                >= minimum_overlap
                            ]
                            folded_chart_question = _fold_text(question)
                            if all(
                                marker in folded_chart_question
                                for marker in ("achat", "vente", "devise")
                            ):
                                dual_operation_contexts = [
                                    context
                                    for context in chart_contexts
                                    if all(
                                        marker in _fold_text(
                                            str(context.get("text", ""))
                                        )
                                        for marker in ("achat", "vente", "devise")
                                    )
                                ]
                                if dual_operation_contexts:
                                    chart_contexts = dual_operation_contexts
                        chart_pages = [
                            int(context["pdf_page"]) for context in chart_contexts
                        ]
            except Exception as exc:
                app.logger.warning(
                    "request_id=%s chart_analysis_failed error_type=%s",
                    g.request_id,
                    type(exc).__name__,
                )
                chart_contexts = []

        # Étape 5 : ajouter les passages voisins pour redonner au générateur le
        # contexte parfois coupé par le découpage du PDF.
        generation_results = engine.decorate(
            engine.expand_with_neighbors(results, max_results=12), language
        )

        # Une page déjà citée peut être entièrement scannée : son index contient
        # alors le titre, mais pas le texte à résumer. Pour un suivi comme
        # « résume cette section », on lit localement ces pages peu textuelles et
        # on injecte leur OCR avant les autres extraits. Ce traitement est
        # générique et repose sur la page mémorisée, jamais sur un cas codé en dur.
        document_contexts: list[dict[str, Any]] = []
        if (
            followup
            and not chart_requested
            and followup_pages
            and _content_followup(question)
            and settings.chart_analysis_enabled
        ):
            sparse_pages: list[int] = []
            for page in followup_pages[:2]:
                page_texts = {
                    re.sub(r"\s+", " ", chunk.text).strip()
                    for chunk in engine.chunks
                    if int(chunk.pdf_page) == page
                }
                native_text = " ".join(sorted(page_texts))
                if len(native_text) < 1_200:
                    sparse_pages.append(page)
            sparse_pages = _report_units(sparse_pages, engine.source_registry)
            if sparse_pages:
                try:
                    rendered_document_pages = render_chart_pages(
                        settings.report_path,
                        sparse_pages,
                        settings.chart_cache_path,
                        dpi=settings.chart_render_dpi,
                        renderer_path=settings.pdf_renderer_path,
                    )
                    document_contexts = extract_document_page_contexts(
                        rendered_document_pages,
                        script_path=(
                            Path(__file__).resolve().parents[1]
                            / "scripts"
                            / "chart_ocr.swift"
                        ),
                        ocr_path=settings.chart_ocr_path,
                        maximum_chars=max(settings.chart_ocr_max_chars, 12_000),
                    )
                    generation_results = [
                        *document_contexts,
                        *generation_results,
                    ]
                except Exception as exc:
                    app.logger.warning(
                        "request_id=%s document_ocr_failed error_type=%s",
                        g.request_id,
                        type(exc).__name__,
                    )
        sources = []
        seen_source_pages: set[int] = set()
        source_query = " ".join(retrieval_queries)
        chart_source_items = [
            item
            for page in chart_pages
            for item in search_results
            if int(item["pdf_page"]) == page
        ]
        for item in [*chart_source_items, *results]:
            page = int(item["pdf_page"])
            if page in seen_source_pages:
                continue
            seen_source_pages.add(page)
            entry = engine.source_registry.get(page, {})
            sources.append(
                {
                    "pdf_page": page,
                    "score": item["score"],
                    "excerpt": _source_excerpt(item["text"], source_query),
                    "citation": citation_label(
                        entry.get("source_type", SOURCE_TYPE_PDF),
                        entry.get("title", ""),
                        entry.get("pdf_page", page),
                        language,
                    )
                    if entry
                    else f"p. PDF {page}",
                    "source_type": entry.get("source_type", SOURCE_TYPE_PDF),
                    "source_title": entry.get("title", ""),
                    "source_url": entry.get("url", ""),
                    "source_page": entry.get("pdf_page", page),
                    "source_date": entry.get("published_at", ""),
                }
            )
            if len(sources) >= 5:
                break

        if clarification_needed:
            yield _evenement_reponse(
                {
                    "answer": _clarification_answer(
                        suggestions, language=language
                    ),
                    "sources": sources,
                    "grounded": False,
                    "clarification_needed": True,
                    "suggestions": suggestions,
                    "chart_analysis": False,
                    "chart_pages": [],
                    "language": language,
                }
            )
            return

        if chart_contexts:
            chart_context_pages = {
                int(context["pdf_page"]) for context in chart_contexts
            }
            chart_page_evidence = [
                {
                    "pdf_page": int(chunk.pdf_page),
                    "text": chunk.text,
                    "score": 1.0,
                }
                for chunk in engine.chunks
                if int(chunk.pdf_page) in chart_context_pages
            ]
            chart_answer = explain_chart_locally(
                question,
                chart_contexts,
                [*generation_results, *chart_page_evidence],
                language,
            )
            if chart_answer and language == "ar":
                chart_answer = format_arabic_bidi(chart_answer)
            # Les explications graphiques locales sont actuellement rédigées en
            # français. Pour une question arabe, on laisse la voie documentaire
            # bilingue répondre à partir du texte du rapport plutôt que d'afficher
            # une analyse française ou d'exporter l'OCR local pour traduction.
            if chart_answer and (
                language != "ar"
                or (
                    is_arabic_text(chart_answer)
                    and not untranslated_latin_words(chart_answer)
                )
            ):
                yield _evenement_reponse(
                    {
                        "answer": chart_answer,
                        "sources": sources,
                        "grounded": True,
                        "clarification_needed": False,
                        "suggestions": [],
                        "chart_analysis": True,
                        "chart_pages": sorted(
                            {int(context["pdf_page"]) for context in chart_contexts}
                        ),
                        "language": language,
                    }
                )
                return

        if chart_requested and is_chart_existence_question(question):
            if chart_pages:
                pages_text = ", ".join(str(page) for page in sorted(set(chart_pages)))
                yield _evenement_reponse(
                    {
                        "answer": (
                            (
                                "نعم. يحتوي التقرير على رسم بياني واحد على الأقل "
                                f"يطابق طلبك في صفحة PDF {pages_text}، لكن تعذر إكمال "
                                "تحليله البصري."
                            )
                            if language == "ar"
                            else (
                                "Oui. Le rapport contient au moins un graphique correspondant "
                                f"à votre demande à la page PDF {pages_text}, mais son analyse "
                                "visuelle n’a pas pu être finalisée."
                            )
                        ),
                        "sources": sources,
                        "grounded": True,
                        "clarification_needed": False,
                        "suggestions": [],
                        "chart_analysis": False,
                        "chart_pages": sorted(set(chart_pages)),
                        "language": language,
                    }
                )
                return
            yield _evenement_reponse(
                {
                    "answer": (
                        "لا. لا أجد رسماً بيانياً يطابق هذا الطلب في تقرير البنك "
                        "المركزي الموريتاني المقدم."
                        if language == "ar"
                        else (
                            "Non. Je ne trouve pas de graphique correspondant à cette demande "
                            "dans les documents BCM fournis."
                        )
                    ),
                    "sources": sources,
                    "grounded": False,
                    "clarification_needed": False,
                    "suggestions": [],
                    "chart_analysis": False,
                    "chart_pages": [],
                    "language": language,
                }
            )
            return

        if not results or not engine.is_relevant(
            search_results, settings.min_relevance_score, settings.min_semantic_score
        ):
            # Rien de pertinent n'a été trouvé. Seules les pistes tirées des
            # libellés réellement présents dans le corpus ont un sens ici : les
            # reformulations produites par le planificateur sont de simples
            # variantes de la question et laisseraient croire, pour une demande
            # hors corpus, que l'assistant pourrait y répondre autrement.
            suggestions = evidence_suggestions
            if suggestions:
                yield _evenement_reponse(
                    {
                        "answer": _clarification_answer(
                            suggestions, missing=True, language=language
                        ),
                        "sources": sources,
                        "grounded": False,
                        "clarification_needed": True,
                        "suggestions": suggestions,
                        "chart_analysis": False,
                        "chart_pages": [],
                        "language": language,
                    }
                )
                return
            yield _evenement_reponse(
                {
                    "answer": missing_information_message(language),
                    "sources": [],
                    "grounded": False,
                    "clarification_needed": False,
                    "suggestions": [],
                    "chart_analysis": False,
                    "chart_pages": [],
                    "language": language,
                }
            )
            return

        # Étape 6 : générer une réponse strictement fondée sur les passages. Une
        # réponse extractive locale reste disponible si le fournisseur échoue.
        yield ("stage", "redaction")
        answer = ""
        try:
            # Les fragments partent au client au fil de la rédaction ; seul
            # l'événement « final » a passé les contrôles de citation.
            for nom_evenement, valeur in stream_answer(
                provider,
                retrieval_question if followup else question,
                generation_results,
                history,
                settings,
                language,
            ):
                if nom_evenement == "delta":
                    yield ("delta", valeur)
                else:
                    answer = valeur
            generation_state["succes"] += 1
            generation_state["dernier_echec"] = None
        except Exception as exc:
            generation_state["echecs"] += 1
            generation_state["dernier_echec"] = type(exc).__name__
            app.logger.warning(
                "request_id=%s generation_failed provider=%s error_type=%s",
                g.request_id,
                provider,
                type(exc).__name__,
            )
            if language == "ar":
                # Le repli extractif ne sait pas traduire le français. Mieux vaut
                # signaler clairement l'indisponibilité que renvoyer silencieusement
                # une réponse dans la mauvaise langue.
                answer = (
                    "تم العثور على مقاطع ذات صلة في التقرير، لكن تعذر إنشاء "
                    "الإجابة بالعربية حالياً. يرجى إعادة المحاولة."
                )
            else:
                answer = answer_with_provider(
                    "extractive",
                    retrieval_question if followup else question,
                    generation_results,
                    history,
                    settings,
                    language,
                )
        if language == "ar":
            answer = format_arabic_bidi(answer)
        # Une réponse qui ne cite aucune source n'est pas fondée : c'est le cas
        # d'un refus rédigé par le modèle. Continuer à publier les passages
        # candidats laisserait croire qu'ils appuient l'affirmation — le widget
        # affichait « cette information est absente » suivi de cinq sources.
        cite_une_source = bool(re.search(r"\[[^\]\n]{2,80}\]", answer))
        yield _evenement_reponse(
            {
                "answer": answer,
                "sources": sources if cite_une_source else [],
                "grounded": cite_une_source,
                "clarification_needed": False,
                "suggestions": [],
                "chart_analysis": False,
                "chart_pages": [],
                "memory_used": followup,
                "language": language,
            }
        )
        return

    def _ask_events_logged() -> Any:
        """Enveloppe `_ask_events` pour journaliser la question et sa réponse.

        Enveloppe plutôt que modifie chaque point de sortie de `_ask_events`
        (une douzaine) : la journalisation se pose donc une seule fois, sans
        risquer d'en oublier une lors d'une future correction du pipeline.
        Best-effort et soumise au consentement transmis par le client — une
        panne de la base ne doit jamais faire échouer une réponse déjà
        produite pour l'utilisateur.
        """
        payload = request.get_json(silent=True)
        session_id = ""
        consent = False
        question_text = ""
        if isinstance(payload, dict):
            brut = payload.get("session_id")
            session_id = brut.strip()[:128] if isinstance(brut, str) else ""
            consent = bool(payload.get("consent_analytics"))
            brut_question = payload.get("question")
            question_text = brut_question.strip() if isinstance(brut_question, str) else ""

        for nom, charge in _ask_events():
            if nom == "done" and consent and session_id and question_text:
                try:
                    log_question(
                        settings,
                        session_id,
                        str(charge.get("language", "fr")),
                        question_text,
                        str(charge.get("answer", "")),
                    )
                except Exception as exc:
                    app.logger.warning(
                        "request_id=%s analytics_log_failed error_type=%s",
                        g.get("request_id"),
                        type(exc).__name__,
                    )
            yield nom, charge

    @app.post("/api/ask")
    @limiter.limit(settings.rate_limit_ask)
    def ask() -> Any:
        """Répond en une seule fois : consomme les événements et renvoie le dernier."""
        for nom, charge in _ask_events_logged():
            if nom == "error":
                message, status, reason = charge
                return error_payload(message, status, reason)
            if nom == "done":
                return jsonify(charge)
        return error_payload("Aucune réponse n'a été produite.", 500)

    @app.post("/api/ask/stream")
    @limiter.limit(settings.rate_limit_ask)
    def ask_stream() -> Any:
        """Diffuse la réponse au fil de sa rédaction, au format Server-Sent Events.

        Le temps total ne change pas : la recherche, la planification et le
        reranking restent des préalables. C'est l'attente perçue qui change,
        l'utilisateur voyant les premiers mots au lieu d'un écran figé.

        Le texte diffusé est provisoire : le client doit le remplacer par la
        valeur de l'événement « done », seule à avoir passé les contrôles de
        citation et, en arabe, le rendu bidirectionnel.
        """

        def encoder(nom: str, charge: Any) -> str:
            """Sérialise un événement au format SSE, sur une seule ligne de données."""
            return f"event: {nom}\ndata: {json.dumps(charge, ensure_ascii=False)}\n\n"

        def flux() -> Any:
            try:
                for nom, charge in _ask_events_logged():
                    if nom == "delta":
                        yield encoder("delta", {"text": charge})
                    elif nom == "stage":
                        yield encoder("stage", {"stage": charge})
                    elif nom == "done":
                        yield encoder("done", charge)
                    elif nom == "error":
                        message, status, reason = charge
                        corps = {"error": message, "status": status}
                        if reason:
                            corps["reason"] = reason
                        yield encoder("error", corps)
                        return
            except Exception as exc:
                app.logger.exception(
                    "request_id=%s stream_failed error_type=%s",
                    g.get("request_id"),
                    type(exc).__name__,
                )
                yield encoder(
                    "error",
                    {"error": "Une erreur interne est survenue.", "status": 500},
                )

        return Response(
            stream_with_context(flux()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # Sans cet en-tête, nginx tamponne la réponse et annule tout le
                # bénéfice de la diffusion.
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/reindex")
    def reindex() -> Any:
        """Reconstruit les index lexical et sémantique après changement du PDF."""
        if settings.is_production:
            authorization = request.headers.get("Authorization", "")
            expected = f"Bearer {settings.reindex_token}" if settings.reindex_token else ""
            if not expected or not hmac.compare_digest(authorization, expected):
                return error_payload("Réindexation non autorisée.", 403)
        with engine_lock:
            metadata = engine.build()
            if settings.semantic_retrieval:
                matrix = embed_documents(
                    [chunk.text for chunk in engine.chunks],
                    model=settings.embedding_model,
                    cache_path=settings.embedding_cache_path,
                    batch_size=settings.embedding_batch_size,
                )
                engine.attach_semantic_embeddings(matrix, settings.embedding_model)
                metadata = engine.metadata
        return jsonify({"status": "ok", **metadata})

    @app.errorhandler(400)
    def invalid_request(error: BadRequest) -> Any:
        """Retourne une erreur lisible lorsque le JSON reçu est invalide."""
        return error_payload("Le corps de la requête est invalide ou n'est pas un JSON valide.", 400)

    @app.errorhandler(404)
    def not_found(_: HTTPException) -> Any:
        """Signale une route inexistante dans le format d'erreur commun."""
        return error_payload("Ressource introuvable.", 404)

    @app.errorhandler(403)
    def forbidden(_: HTTPException) -> Any:
        """Signale un refus d'accès, notamment pour la réindexation."""
        return error_payload("Accès non autorisé.", 403)

    @app.errorhandler(405)
    def method_not_allowed(_: HTTPException) -> Any:
        """Signale qu'une route existe mais pas pour la méthode HTTP utilisée."""
        return error_payload("Méthode HTTP non autorisée.", 405)

    @app.errorhandler(413)
    def too_large(_: HTTPException) -> Any:
        """Refuse proprement une requête qui dépasse la taille autorisée."""
        return error_payload("Requête trop volumineuse.", 413)

    @app.errorhandler(429)
    def rate_limited(_: HTTPException) -> Any:
        """Signale qu'un client a dépassé la limite d'appels autorisée."""
        return error_payload("Trop de requêtes. Réessayez plus tard.", 429)

    @app.errorhandler(Exception)
    def unexpected_error(error: Exception) -> Any:
        """Masque les détails internes et journalise les erreurs inattendues."""
        if isinstance(error, HTTPException):
            return error_payload("La requête ne peut pas être traitée.", error.code or 500)
        app.logger.exception(
            "request_id=%s unhandled_error error_type=%s",
            g.get("request_id"),
            type(error).__name__,
        )
        return error_payload("Erreur interne. Utilisez l'identifiant de requête pour le support.", 500)

    return app


app = create_app()


if __name__ == "__main__":
    active_settings = app.extensions["bcm_settings"]
    app.run(
        host=active_settings.api_host,
        port=active_settings.api_port,
        debug=False,
        threaded=True,
    )
