"""Termes du corpus BCM qui désignent plusieurs indicateurs distincts.

Le planificateur LLM signale rarement une ambiguïté : il choisit en silence une
interprétation (« le solde » devient le solde budgétaire, « les dépôts des
banques » les dépôts de la clientèle). Pour quelques termes dont les sens
possibles figurent tous dans les documents, la décision est donc prise ici, de
façon déterministe : la question est ambiguë si elle emploie le terme sans
aucun des mots qui en fixent le sens. Chaque reformulation proposée a été
vérifiée sur le corpus : elle trouve une réponse sourcée.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable


def _fold(value: str) -> str:
    """Minuscules sans accents, apostrophes typographiques normalisées."""
    normalized = unicodedata.normalize("NFKD", value.casefold().replace("’", "'"))
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _period(question: str, default: str) -> str:
    """Reprend la période de la question dans les reformulations proposées."""
    years = sorted(set(re.findall(r"\b20\d{2}\b", question)))
    if len(years) >= 2:
        return f"entre {years[0]} et {years[-1]}"
    if years:
        return f"en {years[0]}"
    return default


@dataclass(frozen=True)
class AmbiguousTerm:
    """Terme polysémique, mots qui le désambiguïsent et sens possibles."""

    matches: Callable[[str], bool]
    qualifiers: tuple[str, ...]
    explanation: str
    suggestions: Callable[[str], list[str]]


# Mots qui, à eux seuls, ne précisent aucun sens de « taux ».
_TAUX_FILLERS = {
    "quel", "quelle", "est", "etait", "le", "la", "les", "du", "de", "des",
    "actuel", "actuellement", "aujourd", "hui", "taux", "bcm", "en", "a",
    "ete", "maintenant", "mauritanie", "c", "qu", "donne", "moi",
}


def _bare_taux(folded: str) -> bool:
    """« Quel est le taux ? » : aucun autre mot ne dit de quel taux il s'agit."""
    words = re.findall(r"[a-z0-9]+", folded)
    if "taux" not in words:
        return False
    return all(word in _TAUX_FILLERS or re.fullmatch(r"20\d{2}", word) for word in words)


AMBIGUOUS_TERMS: tuple[AmbiguousTerm, ...] = (
    AmbiguousTerm(
        # « dépôt(s) », mais aussi « déposé », « déposent ».
        matches=lambda folded: re.search(r"\bdepo[st]", folded) is not None
        and re.search(r"\bbanque|\bbancaire", folded) is not None,
        qualifiers=(
            "client", "menage", "entreprise", "particulier", "public",
            "a vue", "a terme", "epargne", "collecte", "assujetti",
            "aupres de la bcm", "a la bcm", "banque centrale", "facilite",
            "reserve", "etablissements financiers", "garantie des depots",
            "monnaie electronique", "wallet",
        ),
        explanation=(
            "« Dépôts » et « banques » peuvent désigner deux choses dans les "
            "documents de la BCM : l'argent que les ménages et les entreprises "
            "déposent dans les banques, ou l'argent que les banques elles-mêmes "
            "déposent à la Banque Centrale."
        ),
        suggestions=lambda question: [
            "Comment ont évolué les dépôts de la clientèle dans les banques "
            f"{_period(question, 'entre 2024 et 2025')} ?",
            "Comment ont évolué les placements des banques à la Banque Centrale "
            "(réserves, facilités de dépôt, compte courant) "
            f"{_period(question, 'en 2025')} ?",
        ],
    ),
    AmbiguousTerm(
        matches=lambda folded: re.search(r"\bsoldes?\b", folded) is not None,
        qualifiers=(
            "budget", "courant", "commerci", "balance", "primaire", "global",
            "exterieur", "paiement", "compte",
        ),
        explanation=(
            "« Solde » peut désigner plusieurs soldes publiés par la BCM : celui "
            "du budget de l'État, celui des échanges courants avec l'extérieur "
            "ou celui des échanges de marchandises."
        ),
        suggestions=lambda question: [
            f"Quel a été le solde budgétaire de l'État {_period(question, 'en 2025')} ?",
            f"Quel a été le déficit du compte courant {_period(question, 'en 2025')} ?",
            "Quel a été le solde de la balance commerciale "
            f"{_period(question, 'en 2025')} ?",
        ],
    ),
    AmbiguousTerm(
        matches=lambda folded: re.search(r"\breserves?\b", folded) is not None,
        qualifiers=(
            "change", "officiel", "obligatoire", "libre", "devise", "exterieur",
            "brut", "importation", "dollar", "usd", "liquidite",
            "assurance", "technique", "comptable", "statutaire", "fonds propres",
            # Gestion des réserves de change par la BCM : le sens est déjà fixé.
            "place", "gestion", "confie", "mandat", "notation", "titre",
            "rendement", "revenu", "portefeuille",
            "capitaux",
        ),
        explanation=(
            "« Réserves » peut désigner les réserves de change du pays, gérées "
            "par la BCM, ou les réserves que les banques doivent constituer "
            "auprès de la BCM."
        ),
        suggestions=lambda question: [
            "Quel était le niveau des réserves officielles de change "
            f"{_period(question, 'fin 2025')} ?",
            "Comment ont évolué les réserves obligatoires des banques auprès de la BCM "
            f"{_period(question, 'en 2025')} ?",
        ],
    ),
    AmbiguousTerm(
        matches=_bare_taux,
        qualifiers=(),
        explanation=(
            "« Le taux » peut désigner plusieurs indicateurs suivis par la BCM."
        ),
        suggestions=lambda question: [
            "Quel est le taux directeur de la BCM ?",
            f"Quel a été le taux d'inflation moyen {_period(question, 'en 2025')} ?",
            "Quel a été le taux de croissance du PIB réel "
            f"{_period(question, 'en 2025')} ?",
        ],
    ),
)


def lexical_ambiguity(question: str) -> dict[str, object] | None:
    """Renvoie l'explication et les reformulations si la question est ambiguë.

    Réservé au français : les sens possibles et leurs reformulations sont
    rédigés en français, et le planificateur traite déjà l'arabe.
    """
    folded = _fold(question)
    for term in AMBIGUOUS_TERMS:
        if not term.matches(folded):
            continue
        if any(qualifier in folded for qualifier in term.qualifiers):
            continue
        return {
            "explanation": term.explanation,
            "suggestions": term.suggestions(question),
        }
    return None
