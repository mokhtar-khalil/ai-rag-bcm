"""Détection légère de langue et messages bilingues de l'application.

Le rapport reste indexé en français. La langue détectée sert uniquement à
choisir la langue de restitution et des messages déterministes de l'API.
"""

from __future__ import annotations

import re


ARABIC_CHARACTER = re.compile(
    r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]"
)
LTR_ISOLATE = "\u2066"
POP_DIRECTIONAL_ISOLATE = "\u2069"


def is_arabic_text(value: str) -> bool:
    """Retourne vrai dès qu'une formulation contient réellement de l'arabe."""
    return len(ARABIC_CHARACTER.findall(value or "")) >= 2


def response_language(value: str) -> str:
    """Retourne le code de langue de réponse pris en charge par l'interface."""
    return "ar" if is_arabic_text(value) else "fr"


def missing_information_message(language: str) -> str:
    """Produit le refus documentaire dans la langue de l'utilisateur."""
    if language == "ar":
        return (
            "لا أجد هذه المعلومة في وثائق البنك المركزي الموريتاني المتاحة "
            "(التقرير السنوي والرسائل الإخبارية)."
        )
    return (
        "Je ne trouve pas cette information dans les documents BCM fournis "
        "(Rapport annuel et Lettres d'information)."
    )


def answer_language_instruction(value: str, language: str | None = None) -> str:
    """Construit la consigne de restitution choisie par l'interface.

    ``language`` est prioritaire lorsqu'il vaut ``fr`` ou ``ar``. L'analyse du
    texte reste disponible pour les anciens clients API qui n'envoient pas ce
    paramètre.
    """
    selected = language if language in {"fr", "ar"} else response_language(value)
    if selected == "ar":
        return (
            "لغة الإجابة الإلزامية: العربية الفصحى. أجب بالعربية حتى لو كانت "
            "المصادر بالفرنسية. ترجم جميع الكلمات الفرنسية ووحدات القياس إلى العربية، "
            "واحتفظ بالأرقام وأسماء المؤسسات والمعايير والاختصارات الرسمية بدقة، "
            "واستخدم صيغة الاستشهاد [p. PDF N]."
        )
    return "Langue de réponse obligatoire : français."


ALLOWED_LATIN_ACRONYMS = {
    "ACH", "API", "ATS", "BCM", "EUR", "FMI", "GIM", "GIMTEL", "IFRS",
    "ISA", "ISO", "MRU", "PAFHD", "PDF", "QR", "RTGS", "SWIFT", "UEMOA",
    "UMEF", "USD",
}

FRENCH_RESIDUAL_WORDS = {
    "avec", "contre", "dans", "de", "des", "du", "en", "entre", "est", "et",
    "hausse", "baisse", "le", "la", "les", "milliard", "milliards", "million",
    "millions", "par", "pour", "progression", "recul", "soit", "sur", "valeur",
}


def untranslated_latin_words(value: str) -> list[str]:
    """Repère les mots français restés dans une réponse arabe.

    Les sigles officiels et la syntaxe des citations PDF sont autorisés. Le
    contrôle cible les résidus français connus au lieu de rejeter aveuglément
    tout alphabet latin, car les normes et systèmes de paiement gardent souvent
    leur nom officiel dans une réponse arabe.
    """
    without_citations = re.sub(r"\[p\.\s*PDF\s*\d+\]", " ", value, flags=re.I)
    words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", without_citations)
    return [
        word
        for word in words
        if word.upper() not in ALLOWED_LATIN_ACRONYMS
        and word.casefold() in FRENCH_RESIDUAL_WORDS
    ]


def normalize_arabic_units(value: str) -> str:
    """Nettoie localement les fragments français parfois recopiés des sources."""
    replacements = (
        (r"\bmilliards?\s+de\s+MRU\b", "مليار أوقية موريتانية"),
        (r"\bmilliards?\s+MRU\b", "مليار أوقية موريتانية"),
        (r"\bmillions?\s+de\s+MRU\b", "مليون أوقية موريتانية"),
        (r"\bmillions?\s+MRU\b", "مليون أوقية موريتانية"),
    )
    normalized = value
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.I)
    # Les modèles peuvent conserver le sigle après un mot arabe, par exemple
    # « مليار MRU ». L'unité arabe évite un changement de direction au milieu
    # du montant et reste plus naturelle pour le lecteur.
    normalized = re.sub(
        r"\bMRU\b", "أوقية موريتانية", normalized, flags=re.IGNORECASE
    )
    normalized = re.sub(r"(?<=\d)\s*%", "٪", normalized)
    word_replacements = {
        "avec": "مع",
        "contre": "مقابل",
        "dans": "في",
        "de": "",
        "des": "",
        "du": "",
        "en": "في",
        "entre": "بين",
        "est": "هو",
        "et": "و",
        "hausse": "ارتفاع",
        "baisse": "انخفاض",
        "le": "",
        "la": "",
        "les": "",
        "par": "حسب",
        "pour": "من أجل",
        "progression": "زيادة",
        "recul": "تراجع",
        "soit": "أي",
        "sur": "على",
        "valeur": "قيمة",
    }
    for french, arabic in word_replacements.items():
        normalized = re.sub(
            rf"\b{french}\b", arabic, normalized, flags=re.IGNORECASE
        )
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    return normalized


def format_arabic_bidi(value: str) -> str:
    """Isole les fragments gauche-à-droite dans une réponse arabe.

    Les navigateurs appliquent l'algorithme bidirectionnel Unicode : sans
    isolation, une année ou une citation latine peut apparaître au début de la
    mauvaise proposition. LRI/PDI garde chaque nombre, norme et citation comme
    un bloc visuel, sans ajouter de balise HTML au texte de l'API.
    """
    normalized = normalize_arabic_units(value)
    if not is_arabic_text(normalized):
        return normalized

    # Retirer d'anciens isolats évite leur imbrication lorsque l'historique est
    # renvoyé à l'API puis restitué une seconde fois.
    normalized = normalized.replace(LTR_ISOLATE, "").replace(
        POP_DIRECTIONAL_ISOLATE, ""
    )
    protected: dict[str, str] = {}

    def protect(match: re.Match[str]) -> str:
        marker = chr(0xE000 + len(protected))
        protected[marker] = match.group(0)
        return marker

    # Une citation doit rester un seul bloc ; son numéro ne doit pas être isolé
    # séparément de « p. PDF ».
    normalized = re.sub(
        r"\[p\.\s*PDF\s*\d+\]", protect, normalized, flags=re.IGNORECASE
    )
    # Isoler ensemble les normes qui associent lettres et chiffres.
    normalized = re.sub(
        r"(?<![A-Za-z0-9])(?:ISO|IFRS|ISA)\s+\d+(?:[-:]\d+)?(?![A-Za-z0-9])",
        protect,
        normalized,
        flags=re.IGNORECASE,
    )
    # Un montant, une année ou un pourcentage occidental devient une unité
    # directionnelle indivisible. Le signe arabe ٪ est inclus dans le bloc.
    def isolate_number(match: re.Match[str]) -> str:
        # Une espace fine insécable empêche « 300 000 » d'être coupé entre deux
        # lignes tout en conservant le regroupement des milliers.
        number = re.sub(r"(?<=\d)[ \u00a0](?=\d)", "\u202f", match.group(0))
        return f"{LTR_ISOLATE}{number}{POP_DIRECTIONAL_ISOLATE}"

    normalized = re.sub(
        r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+(?:[.,]\d+)*)(?:\s*٪)?(?![A-Za-z0-9])",
        isolate_number,
        normalized,
    )
    # Les sigles officiels restants se lisent de gauche à droite.
    normalized = re.sub(
        r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9-]{1,}(?![A-Za-z0-9])",
        lambda match: (
            f"{LTR_ISOLATE}{match.group(0)}{POP_DIRECTIONAL_ISOLATE}"
        ),
        normalized,
    )
    for marker, fragment in protected.items():
        normalized = normalized.replace(
            marker, f"{LTR_ISOLATE}{fragment}{POP_DIRECTIONAL_ISOLATE}"
        )
    return normalized
