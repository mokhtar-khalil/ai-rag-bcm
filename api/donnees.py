"""Voie des données chiffrées : séries datées lues dans une base, jamais générées.

Une question comme « Quel est le cours de l'euro aujourd'hui ? » ne passe ni par
la recherche documentaire ni par le modèle. Un tableau de cours découpé en
passages perd l'alignement entre une date et sa valeur, et un modèle qui
recopie un chiffre peut le déformer. La chaîne est donc entièrement
déterministe :

1. des règles reconnaissent la série, la date ou la période et l'opération ;
2. une requête prise dans une liste fermée lit la base SQLite ;
3. Python calcule variation, minimum, maximum et moyenne ;
4. un gabarit rédige la réponse, en français ou en arabe.

Le contrôle `nombres_non_justifies` vérifie qu'un texte ne contient aucun
nombre absent des faits calculés. Il sert de garde-fou à la future rédaction
d'une analyse par le modèle, et de contrôle de cohérence aux gabarits.

Les dates sont résolues à l'heure de Nouakchott, qui est UTC+0 toute l'année.
"""

from __future__ import annotations

import calendar
import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    code TEXT PRIMARY KEY,
    libelle_fr TEXT NOT NULL,
    libelle_ar TEXT NOT NULL,
    court_fr TEXT NOT NULL,
    court_ar TEXT NOT NULL,
    unite TEXT NOT NULL,
    unite_ar TEXT NOT NULL,
    decimales INTEGER NOT NULL DEFAULT 2,
    -- « quotidienne » : une valeur par jour ouvré ; « evenement » : une valeur
    -- qui reste en vigueur jusqu'à la décision suivante (taux directeur).
    frequence TEXT NOT NULL,
    page_source TEXT NOT NULL,
    synonymes TEXT NOT NULL,
    est_test INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS observations (
    code TEXT NOT NULL REFERENCES series(code),
    date_valeur TEXT NOT NULL,
    valeur REAL NOT NULL,
    -- Une valeur corrigée par la BCM arrive avec une date de publication plus
    -- récente : l'ancienne reste en base, la plus récente fait foi.
    date_publication TEXT NOT NULL,
    PRIMARY KEY (code, date_valeur, date_publication)
);
CREATE TABLE IF NOT EXISTS synchronisations (
    execute_le TEXT NOT NULL,
    source TEXT NOT NULL,
    lignes_ajoutees INTEGER NOT NULL,
    lignes_corrigees INTEGER NOT NULL,
    erreurs TEXT NOT NULL DEFAULT ''
);
"""

FREQUENCE_QUOTIDIENNE = "quotidienne"
FREQUENCE_EVENEMENT = "evenement"

# Au-delà, une série quotidienne est signalée comme en retard de publication.
RETARD_TOLERE_JOURS = 4
# Une demande d'évolution sans période explicite porte sur ce nombre de jours.
PERIODE_PAR_DEFAUT_JOURS = 30
# Le widget n'affiche qu'un aperçu : la réponse JSON n'a pas à porter des années.
POINTS_MAX = 400

MOIS_FR = (
    "janvier", "fevrier", "mars", "avril", "mai", "juin",
    "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
)
MOIS_FR_AFFICHES = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)
MOIS_AR = (
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
)
JOURS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

# Sans l'un de ces marqueurs, citer une devise ne suffit pas : « les réformes de
# la zone euro » reste une question documentaire.
MARQUEURS_DONNEE = {
    "cours", "change", "taux", "combien", "vaut", "valeur", "niveau", "cotation",
    "mru", "ouguiya", "ouguiyas", "actuel", "actuellement", "evolution", "evolue",
    "variation", "moyenne",
}
MARQUEURS_DONNEE_AR = ("سعر", "صرف", "كم", "قيمة", "تطور", "متوسط")

# Une explication, une décision ou un document cité relèvent du RAG : le
# communiqué dit pourquoi le taux a changé, la base ne dit que de combien.
MARQUEURS_DOCUMENTAIRES = {
    "pourquoi", "explique", "expliquer", "raison", "raisons", "decision",
    "decisions", "impact", "effet", "effets", "consequence", "consequences",
    "rapport", "lettre", "graphique", "comite", "politique",
}
MARQUEURS_DOCUMENTAIRES_AR = ("لماذا", "اشرح", "قرار", "تقرير", "رسالة", "سبب")

MARQUEURS_EVOLUTION = {
    "evolution", "evolue", "varie", "variation", "tendance", "moyenne", "moyen",
    "hausse", "baisse", "progression", "historique", "depuis", "entre",
}
MARQUEURS_EVOLUTION_AR = ("تطور", "تغير", "متوسط", "منذ", "بين")

# Mots qu'une question trop vague (« Quel est le taux actuel ? ») peut contenir
# sans nommer de série : on demande alors laquelle au lieu de deviner.
MOTS_QUESTION_VAGUE = {
    "quel", "quelle", "est", "le", "la", "les", "taux", "cours", "actuel",
    "actuellement", "du", "jour", "aujourd", "hui", "combien", "c", "d", "l",
    "de", "change", "en", "ce", "moment", "?",
}

SUGGESTIONS_VAGUES = {
    "fr": (
        "Quel est le taux directeur de la BCM ?",
        "Quel est le cours de l'euro aujourd'hui ?",
        "Quel est le cours du dollar aujourd'hui ?",
    ),
    "ar": (
        "ما هو سعر الفائدة الرئيسي للبنك المركزي الموريتاني؟",
        "ما هو سعر صرف اليورو اليوم؟",
        "ما هو سعر صرف الدولار اليوم؟",
    ),
}


@dataclass(frozen=True)
class Serie:
    """Série publiée par la BCM, telle que décrite dans la table `series`."""

    code: str
    libelle_fr: str
    libelle_ar: str
    court_fr: str
    court_ar: str
    unite: str
    unite_ar: str
    decimales: int
    frequence: str
    page_source: str
    synonymes: tuple[str, ...]
    est_test: bool = False


@dataclass(frozen=True)
class Observation:
    """Valeur d'une série à une date, avec la date à laquelle elle a été publiée."""

    date_valeur: date
    valeur: float
    date_publication: date


@dataclass(frozen=True)
class Periode:
    """Date ou intervalle demandé ; `debut` vaut None pour une valeur ponctuelle."""

    fin: date
    debut: date | None = None
    explicite: bool = False


@dataclass(frozen=True)
class DemandeDonnees:
    """Question reconnue comme portant sur une ou plusieurs séries chiffrées."""

    series: tuple[Serie, ...]
    operation: str
    periode: Periode


@dataclass(frozen=True)
class AmbiguiteDonnees:
    """Question chiffrée qui ne nomme pas de série : on propose des reformulations."""

    langue: str


@dataclass
class ReponseDonnees:
    """Réponse rédigée par gabarit, ses sources et les faits qui l'appuient."""

    answer: str
    grounded: bool
    sources: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    nombres_autorises: set[str] = field(default_factory=set)


def aujourd_hui() -> date:
    """Date du jour à Nouakchott (UTC+0, sans heure d'été)."""
    return datetime.now(timezone.utc).date()


# ---- Base -------------------------------------------------------------------


def creer_schema(conn: sqlite3.Connection) -> None:
    """Crée les tables si elles n'existent pas encore."""
    conn.executescript(SCHEMA)


def inserer_serie(conn: sqlite3.Connection, serie: Serie) -> None:
    """Ajoute ou remplace la description d'une série."""
    conn.execute(
        "INSERT OR REPLACE INTO series VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            serie.code, serie.libelle_fr, serie.libelle_ar, serie.court_fr,
            serie.court_ar, serie.unite, serie.unite_ar, serie.decimales,
            serie.frequence, serie.page_source,
            json.dumps(list(serie.synonymes), ensure_ascii=False),
            int(serie.est_test),
        ),
    )


def inserer_observations(
    conn: sqlite3.Connection, code: str, observations: Iterable[Observation]
) -> int:
    """Ajoute des valeurs ; une valeur déjà connue pour la même publication est ignorée."""
    avant = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?)",
        [
            (code, item.date_valeur.isoformat(), item.valeur, item.date_publication.isoformat())
            for item in observations
        ],
    )
    return conn.total_changes - avant


def ouvrir_lecture(path: Path) -> sqlite3.Connection:
    """Ouvre la base en lecture seule : le service ne doit jamais la modifier."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def charger_catalogue(path: Path) -> tuple[Serie, ...]:
    """Lit la liste des séries ; une base absente donne un catalogue vide."""
    if not path.is_file():
        return ()
    with ouvrir_lecture(path) as conn:
        lignes = conn.execute(
            "SELECT code, libelle_fr, libelle_ar, court_fr, court_ar, unite, unite_ar,"
            " decimales, frequence, page_source, synonymes, est_test FROM series"
            " ORDER BY code"
        ).fetchall()
    return tuple(
        Serie(
            code=ligne[0], libelle_fr=ligne[1], libelle_ar=ligne[2],
            court_fr=ligne[3], court_ar=ligne[4], unite=ligne[5], unite_ar=ligne[6],
            decimales=int(ligne[7]), frequence=ligne[8], page_source=ligne[9],
            synonymes=tuple(json.loads(ligne[10])), est_test=bool(ligne[11]),
        )
        for ligne in lignes
    )


def _observation(ligne: tuple[Any, ...]) -> Observation:
    return Observation(
        date_valeur=date.fromisoformat(ligne[0]),
        valeur=float(ligne[1]),
        date_publication=date.fromisoformat(ligne[2]),
    )


# Les seules requêtes que le service exécute. Aucune n'est construite à partir
# du texte de la question : seuls le code de série et les dates sont liés.
_REQUETE_DERNIERE_VALEUR = (
    "SELECT date_valeur, valeur, date_publication FROM observations"
    " WHERE code = ? AND date_valeur <= ?"
    " ORDER BY date_valeur DESC, date_publication DESC LIMIT 1"
)
_REQUETE_PREMIERE_DATE = "SELECT MIN(date_valeur) FROM observations WHERE code = ?"
_REQUETE_INTERVALLE = (
    "SELECT o.date_valeur, o.valeur, o.date_publication FROM observations o"
    " WHERE o.code = ? AND o.date_valeur BETWEEN ? AND ?"
    " AND o.date_publication = ("
    "   SELECT MAX(p.date_publication) FROM observations p"
    "   WHERE p.code = o.code AND p.date_valeur = o.date_valeur)"
    " ORDER BY o.date_valeur"
)


def derniere_valeur(conn: sqlite3.Connection, code: str, jusqu_au: date) -> Observation | None:
    """Dernière valeur publiée à la date demandée ou avant."""
    ligne = conn.execute(_REQUETE_DERNIERE_VALEUR, (code, jusqu_au.isoformat())).fetchone()
    return _observation(ligne) if ligne else None


def premiere_date(conn: sqlite3.Connection, code: str) -> date | None:
    """Début de l'historique disponible pour une série."""
    ligne = conn.execute(_REQUETE_PREMIERE_DATE, (code,)).fetchone()
    return date.fromisoformat(ligne[0]) if ligne and ligne[0] else None


def valeurs_intervalle(
    conn: sqlite3.Connection, code: str, debut: date, fin: date
) -> list[Observation]:
    """Valeurs de l'intervalle, dans leur dernière version publiée."""
    lignes = conn.execute(
        _REQUETE_INTERVALLE, (code, debut.isoformat(), fin.isoformat())
    ).fetchall()
    return [_observation(ligne) for ligne in lignes]


# ---- Détection --------------------------------------------------------------


def _plier(texte: str) -> str:
    """Minuscules sans accents ni hamza, apostrophes unifiées, espaces réduits."""
    texte = texte.replace("’", "'").replace("ʼ", "'")
    decompose = unicodedata.normalize("NFKD", texte.casefold())
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sans_accents).strip()


def _contient_arabe(texte: str) -> bool:
    return bool(re.search(r"[؀-ۿ]", texte))


def _contient_terme(question_pliee: str, terme: str) -> bool:
    """Un terme latin doit former un mot entier ; un terme arabe peut porter un préfixe."""
    terme = _plier(terme)
    if _contient_arabe(terme) or not terme[:1].isalnum():
        # « اليورو », « باليورو » : l'article et les prépositions arabes se
        # soudent au mot ; « € » n'a pas de limite de mot.
        return terme in question_pliee
    return bool(re.search(rf"(?<!\w){re.escape(terme)}(?!\w)", question_pliee))


def _mots(question_pliee: str) -> set[str]:
    """Mots latins ; l'apostrophe sépare (« l'euro », « d'evolution »)."""
    mots = set(re.findall(r"[a-z0-9]+", question_pliee))
    if "aujourd'hui" in question_pliee:
        mots.add("aujourd'hui")
    return mots


_PREFIXES_ARABES = ("وال", "بال", "فال", "لل", "ال", "و", "ب", "ل", "ف")


def _mots_arabes(question_pliee: str) -> set[str]:
    """Mots arabes débarrassés d'un préfixe soudé (article, conjonction, préposition)."""
    mots: set[str] = set()
    # Lettres seules : « ؟ » et « ، » appartiennent aussi au bloc arabe.
    for mot in re.findall(r"[ء-يٱ-ۓ]+", question_pliee):
        mots.add(mot)
        for prefixe in _PREFIXES_ARABES:
            if mot.startswith(prefixe) and len(mot) - len(prefixe) >= 2:
                mots.add(mot[len(prefixe):])
                break
    return mots


def _marqueur_arabe(question_pliee: str, marqueurs: Iterable[str]) -> bool:
    """Un marqueur arabe doit commencer un mot : « كم » ne se lit pas dans « الحكم »."""
    mots = _mots_arabes(question_pliee)
    return any(
        mot.startswith(_plier(marqueur)) and len(mot) - len(marqueur) <= 3
        for mot in mots
        for marqueur in marqueurs
    )


_MOIS = "|".join(MOIS_FR)
_MOTIF_DATE = re.compile(
    r"(?P<iso>\b(?P<iso_a>\d{4})-(?P<iso_m>\d{2})-(?P<iso_j>\d{2})\b)"
    r"|(?P<num>\b(?P<num_j>\d{1,2})/(?P<num_m>\d{1,2})/(?P<num_a>\d{4})\b)"
    rf"|(?P<jma>\b(?P<jma_j>\d{{1,2}})(?:er)? (?P<jma_m>{_MOIS}) (?P<jma_a>\d{{4}})\b)"
    rf"|(?P<jm>\b(?P<jm_j>\d{{1,2}})(?:er)? (?P<jm_m>{_MOIS})\b)"
    rf"|(?P<fin>\bfin (?P<fin_a>(?:19|20)\d{{2}})\b)"
    rf"|(?P<ma>\b(?P<ma_m>{_MOIS})(?: (?P<ma_a>\d{{4}}))?\b)"
    r"|(?P<an>\b(?P<an_a>(?:19|20)\d{2})\b)"
)


def _annee_implicite(mois: int, jour: int, reference: date) -> int:
    """Sans année, une date désigne la plus récente occurrence passée."""
    candidate = date(reference.year, mois, min(jour, 28))
    return reference.year if candidate <= reference else reference.year - 1


def _dernier_jour(annee: int, mois: int) -> date:
    return date(annee, mois, calendar.monthrange(annee, mois)[1])


def _intervalles_cites(question_pliee: str, reference: date) -> list[tuple[date, date]]:
    """Dates citées, chacune sous forme d'intervalle : un jour, un mois ou une année."""
    intervalles: list[tuple[date, date]] = []
    for trouve in _MOTIF_DATE.finditer(question_pliee):
        try:
            if trouve["iso"]:
                jour = date(int(trouve["iso_a"]), int(trouve["iso_m"]), int(trouve["iso_j"]))
                intervalles.append((jour, jour))
            elif trouve["num"]:
                jour = date(int(trouve["num_a"]), int(trouve["num_m"]), int(trouve["num_j"]))
                intervalles.append((jour, jour))
            elif trouve["jma"]:
                mois = MOIS_FR.index(trouve["jma_m"]) + 1
                jour = date(int(trouve["jma_a"]), mois, int(trouve["jma_j"]))
                intervalles.append((jour, jour))
            elif trouve["jm"]:
                mois = MOIS_FR.index(trouve["jm_m"]) + 1
                numero = int(trouve["jm_j"])
                jour = date(_annee_implicite(mois, numero, reference), mois, numero)
                intervalles.append((jour, jour))
            elif trouve["fin"]:
                jour = date(int(trouve["fin_a"]), 12, 31)
                intervalles.append((jour, jour))
            elif trouve["ma"]:
                mois = MOIS_FR.index(trouve["ma_m"]) + 1
                annee = (
                    int(trouve["ma_a"]) if trouve["ma_a"]
                    else _annee_implicite(mois, 1, reference)
                )
                intervalles.append((date(annee, mois, 1), _dernier_jour(annee, mois)))
            elif trouve["an"]:
                annee = int(trouve["an_a"])
                intervalles.append((date(annee, 1, 1), date(annee, 12, 31)))
        except ValueError:
            # « 31/02/2026 » : une date impossible est ignorée plutôt que devinée.
            continue
    return intervalles


def resoudre_periode(question: str, reference: date) -> Periode:
    """Traduit les dates de la question en date ou intervalle, sans aucun modèle."""
    q = _plier(question)
    intervalles = _intervalles_cites(q, reference)
    mots = _mots(q)

    if ("entre" in mots or _marqueur_arabe(q, ("بين",))) and len(intervalles) >= 2:
        return Periode(debut=intervalles[0][0], fin=intervalles[1][1], explicite=True)
    if ("depuis" in mots or _marqueur_arabe(q, ("منذ",))) and intervalles:
        return Periode(debut=intervalles[0][0], fin=reference, explicite=True)
    if intervalles:
        debut, fin = intervalles[0]
        if debut == fin:
            return Periode(fin=fin, explicite=True)
        return Periode(debut=debut, fin=fin, explicite=True)
    if re.search(r"\bmois (?:dernier|precedent)\b", q) or "الشهر الماضي" in q:
        fin = reference.replace(day=1) - timedelta(days=1)
        return Periode(debut=fin.replace(day=1), fin=fin, explicite=True)
    if re.search(r"\bcette annee\b|\bdebut de l'annee\b", q) or "هذه السنة" in q:
        return Periode(debut=date(reference.year, 1, 1), fin=reference, explicite=True)
    if "avant-hier" in q:
        return Periode(fin=reference - timedelta(days=2), explicite=True)
    if "hier" in mots or "امس" in _mots_arabes(q):
        return Periode(fin=reference - timedelta(days=1), explicite=True)
    return Periode(fin=reference)


def _question_vague(question_pliee: str) -> bool:
    """« Quel est le taux actuel ? » : un marqueur chiffré, mais aucune série."""
    jetons = re.findall(r"[a-z]+|\?", question_pliee)
    return (
        bool(jetons)
        and bool({"taux", "cours"} & set(jetons))
        and set(jetons) <= MOTS_QUESTION_VAGUE
    )


def detecter_demande(
    question: str,
    catalogue: Iterable[Serie],
    reference: date,
    langue: str = "fr",
) -> DemandeDonnees | AmbiguiteDonnees | None:
    """Reconnaît une question chiffrée ; None la renvoie à la recherche documentaire."""
    q = _plier(question)
    mots = _mots(q)
    if mots & MARQUEURS_DOCUMENTAIRES or _marqueur_arabe(q, MARQUEURS_DOCUMENTAIRES_AR):
        return None

    series = tuple(
        serie for serie in catalogue
        if any(_contient_terme(q, terme) for terme in serie.synonymes)
    )
    if not series:
        return AmbiguiteDonnees(langue) if _question_vague(q) else None

    marqueur = bool(mots & MARQUEURS_DONNEE) or _marqueur_arabe(q, MARQUEURS_DONNEE_AR)
    if not marqueur:
        return None

    periode = resoudre_periode(question, reference)
    evolution = bool(mots & MARQUEURS_EVOLUTION) or _marqueur_arabe(
        q, MARQUEURS_EVOLUTION_AR
    )
    if periode.debut is None and evolution:
        periode = Periode(
            debut=periode.fin - timedelta(days=PERIODE_PAR_DEFAUT_JOURS),
            fin=periode.fin,
            explicite=periode.explicite,
        )
    if periode.debut is not None and periode.fin > reference:
        periode = Periode(debut=periode.debut, fin=reference, explicite=True)
    operation = "evolution" if periode.debut is not None else "valeur"
    return DemandeDonnees(series=series[:3], operation=operation, periode=periode)


def suggestions_ambiguite(langue: str) -> list[str]:
    """Reformulations proposées quand la question ne nomme aucune série."""
    return list(SUGGESTIONS_VAGUES["ar" if langue == "ar" else "fr"])


# ---- Calculs et rédaction -----------------------------------------------------


def calculer_faits(observations: list[Observation]) -> dict[str, Any]:
    """Résume un intervalle : extrémités, variation, plus haut, plus bas, moyenne."""
    premiere, derniere = observations[0], observations[-1]
    plus_haut = max(observations, key=lambda item: (item.valeur, item.date_valeur))
    plus_bas = min(observations, key=lambda item: (item.valeur, item.date_valeur))
    variation = derniere.valeur - premiere.valeur
    return {
        "debut": {"date": premiere.date_valeur.isoformat(), "valeur": premiere.valeur},
        "fin": {"date": derniere.date_valeur.isoformat(), "valeur": derniere.valeur},
        "variation": variation,
        "variation_pct": (variation / premiere.valeur * 100) if premiere.valeur else None,
        "plus_haut": {"date": plus_haut.date_valeur.isoformat(), "valeur": plus_haut.valeur},
        "plus_bas": {"date": plus_bas.date_valeur.isoformat(), "valeur": plus_bas.valeur},
        "moyenne": fmean(item.valeur for item in observations),
        "nombre": len(observations),
    }


def formater_nombre(valeur: float, decimales: int, signe: bool = False) -> str:
    """Virgule décimale et espace fine insécable pour les milliers, comme le site."""
    texte = f"{abs(valeur):,.{decimales}f}".replace(",", " ").replace(".", ",")
    if valeur < 0 and float(texte.replace(" ", "").replace(",", ".")) != 0:
        return f"-{texte}"
    return f"+{texte}" if signe else texte


def formater_date(jour: date, langue: str, avec_jour: bool = False) -> str:
    """« 22 octobre 2026 » ou « 22 أكتوبر 2026 », avec le jour de semaine si demandé."""
    if langue == "ar":
        return f"{jour.day} {MOIS_AR[jour.month - 1]} {jour.year}"
    texte = f"{'1er' if jour.day == 1 else jour.day} {MOIS_FR_AFFICHES[jour.month - 1]} {jour.year}"
    return f"{JOURS_FR[jour.weekday()]} {texte}" if avec_jour else texte


def _normaliser_nombre(texte: str) -> str:
    return re.sub(r"[\s  +\-]", "", texte).replace("er", "")


_MOTIF_NOMBRE = re.compile(r"\d+(?:[   ]\d{3})*(?:,\d+)?(?:er)?")


def nombres_cites(texte: str) -> set[str]:
    """Nombres présents dans un texte, normalisés pour la comparaison."""
    sans_isolats = re.sub(r"[⁦-⁩]", "", texte)
    return {_normaliser_nombre(item) for item in _MOTIF_NOMBRE.findall(sans_isolats)}


def nombres_non_justifies(texte: str, autorises: set[str]) -> list[str]:
    """Nombres du texte absents des faits : une réponse qui en contient est rejetée."""
    permis = {_normaliser_nombre(item) for item in autorises}
    return sorted(nombre for nombre in nombres_cites(texte) if nombre not in permis)


class _Redaction:
    """Accumule les phrases d'une réponse et chaque nombre qu'elles ont le droit de citer."""

    def __init__(self, langue: str) -> None:
        self.langue = langue
        self.paragraphes: list[str] = []
        self.autorises: set[str] = set()

    def nombre(self, valeur: float, decimales: int, signe: bool = False) -> str:
        texte = formater_nombre(valeur, decimales, signe)
        self.autorises.add(texte)
        return texte

    def date(self, jour: date, avec_jour: bool = False) -> str:
        self.autorises.update({str(jour.day), str(jour.year)})
        return formater_date(jour, self.langue, avec_jour)

    def entier(self, valeur: int) -> str:
        self.autorises.add(str(valeur))
        return str(valeur)


def _citation(serie: Serie, langue: str) -> str:
    return (
        f"[بيانات البنك المركزي · {serie.court_ar}]"
        if langue == "ar"
        else f"[Données BCM · {serie.court_fr}]"
    )


def _rediger_valeur(
    r: _Redaction, serie: Serie, obs: Observation | None, periode: Periode,
    debut_historique: date | None, reference: date,
) -> bool:
    """Rédige une valeur ponctuelle ; renvoie False si aucune valeur n'existe."""
    ar = r.langue == "ar"
    libelle = serie.libelle_ar if ar else serie.libelle_fr
    unite = serie.unite_ar if ar else serie.unite
    citation = _citation(serie, r.langue)
    demande = periode.fin

    if obs is None:
        if ar:
            debut = f" يبدأ السجل المتاح في {r.date(debut_historique)}." if debut_historique else ""
            r.paragraphes.append(f"لا تتوفر أي قيمة لـ{libelle} في {r.date(demande)}.{debut}")
        else:
            debut = (
                f" L'historique disponible commence le {r.date(debut_historique)}."
                if debut_historique else ""
            )
            r.paragraphes.append(
                f"Aucune valeur du {libelle} n'est disponible au {r.date(demande)}.{debut}"
            )
        return False

    valeur = f"**{r.nombre(obs.valeur, serie.decimales)} {unite}**"
    if serie.frequence == FREQUENCE_EVENEMENT:
        r.paragraphes.append(
            f"في {r.date(demande)}، بلغ {libelle} {valeur}، وهو ساري المفعول منذ "
            f"{r.date(obs.date_valeur)} {citation}."
            if ar
            else f"Au {r.date(demande)}, le {libelle} est de {valeur}, en vigueur depuis "
            f"le {r.date(obs.date_valeur)} {citation}."
        )
        return True

    if obs.date_valeur == demande:
        r.paragraphes.append(
            f"بلغ {libelle} في {r.date(demande)} {valeur} {citation}."
            if ar
            else f"Au {r.date(demande)}, le {libelle} est de {valeur} {citation}."
        )
    elif periode.explicite:
        # Week-end, jour férié ou publication pas encore faite : la valeur
        # antérieure est donnée, mais jamais présentée comme celle du jour demandé.
        r.paragraphes.append(
            f"لم تُنشر أي قيمة في {r.date(demande)}. آخر قيمة منشورة قبل ذلك، بتاريخ "
            f"{r.date(obs.date_valeur)}، هي {valeur} {citation}."
            if ar
            else f"Aucune valeur n'est publiée pour le {r.date(demande, avec_jour=True)}. "
            f"La dernière valeur publiée auparavant, au {r.date(obs.date_valeur, avec_jour=True)}, "
            f"est de {valeur} {citation}."
        )
    else:
        r.paragraphes.append(
            f"آخر قيمة منشورة لـ{libelle}، بتاريخ {r.date(obs.date_valeur)}، هي {valeur} {citation}."
            if ar
            else f"Dernière valeur publiée : au {r.date(obs.date_valeur)}, le {libelle} est de "
            f"{valeur} {citation}."
        )
    if (reference - obs.date_valeur).days > RETARD_TOLERE_JOURS and not periode.explicite:
        r.paragraphes.append(
            "لا تتوفر أي قيمة أحدث." if ar else "Aucune valeur plus récente n'est disponible."
        )
    return True


def _rediger_evolution(
    r: _Redaction, serie: Serie, observations: list[Observation], periode: Periode,
) -> dict[str, Any] | None:
    """Rédige le résumé d'un intervalle ; renvoie les faits calculés."""
    ar = r.langue == "ar"
    libelle = serie.libelle_ar if ar else serie.libelle_fr
    unite = serie.unite_ar if ar else serie.unite
    citation = _citation(serie, r.langue)
    if not observations:
        assert periode.debut is not None
        r.paragraphes.append(
            f"لا تتوفر أي قيمة لـ{libelle} بين {r.date(periode.debut)} و{r.date(periode.fin)}."
            if ar
            else f"Aucune valeur du {libelle} n'est disponible entre le "
            f"{r.date(periode.debut)} et le {r.date(periode.fin)}."
        )
        return None

    faits = calculer_faits(observations)
    d = serie.decimales
    debut, fin = observations[0], observations[-1]
    en_points = serie.unite == "%"
    variation = r.nombre(faits["variation"], d, signe=True)
    if en_points:
        ecart = f"{variation} نقطة" if ar else f"{variation} point"
    else:
        ecart = f"{variation} {unite}"
        if faits["variation_pct"] is not None:
            ecart += f" ({r.nombre(faits['variation_pct'], 1, signe=True)} %)"
    haut, bas = faits["plus_haut"], faits["plus_bas"]
    if ar:
        r.paragraphes.append(
            f"بين {r.date(debut.date_valeur)} و{r.date(fin.date_valeur)}، انتقل {libelle} من "
            f"{r.nombre(debut.valeur, d)} إلى **{r.nombre(fin.valeur, d)} {unite}**، أي تغير "
            f"قدره {ecart} {citation}.\n"
            f"- أعلى قيمة: {r.nombre(haut['valeur'], d)} {unite} في "
            f"{r.date(date.fromisoformat(haut['date']))}\n"
            f"- أدنى قيمة: {r.nombre(bas['valeur'], d)} {unite} في "
            f"{r.date(date.fromisoformat(bas['date']))}\n"
            f"- المتوسط: {r.nombre(faits['moyenne'], d)} {unite} على "
            f"{r.entier(faits['nombre'])} قيمة منشورة"
        )
    else:
        r.paragraphes.append(
            f"Entre le {r.date(debut.date_valeur)} et le {r.date(fin.date_valeur)}, le {libelle} "
            f"est passé de {r.nombre(debut.valeur, d)} à **{r.nombre(fin.valeur, d)} {unite}**, "
            f"soit une variation de {ecart} {citation}.\n"
            f"- Plus haut : {r.nombre(haut['valeur'], d)} {unite} le "
            f"{r.date(date.fromisoformat(haut['date']))}\n"
            f"- Plus bas : {r.nombre(bas['valeur'], d)} {unite} le "
            f"{r.date(date.fromisoformat(bas['date']))}\n"
            f"- Moyenne : {r.nombre(faits['moyenne'], d)} {unite} sur "
            f"{r.entier(faits['nombre'])} valeurs publiées"
        )
    return faits


def repondre(
    demande: DemandeDonnees, db_path: Path, langue: str, reference: date
) -> ReponseDonnees:
    """Exécute les requêtes de la demande et rédige la réponse par gabarit."""
    r = _Redaction("ar" if langue == "ar" else "fr")
    sources: list[dict[str, Any]] = []
    series_data: list[dict[str, Any]] = []
    une_valeur = False

    with ouvrir_lecture(db_path) as conn:
        for serie in demande.series:
            debut_historique = premiere_date(conn, serie.code)
            if demande.operation == "evolution":
                assert demande.periode.debut is not None
                observations = valeurs_intervalle(
                    conn, serie.code, demande.periode.debut, demande.periode.fin
                )
                faits = _rediger_evolution(r, serie, observations, demande.periode)
                trouve = faits is not None
            else:
                obs = derniere_valeur(conn, serie.code, demande.periode.fin)
                observations = [obs] if obs else []
                faits = None
                trouve = _rediger_valeur(
                    r, serie, obs, demande.periode, debut_historique, reference
                )
            une_valeur = une_valeur or trouve
            derniere_publication = max(
                (item.date_publication for item in observations), default=None
            )
            if trouve:
                sources.append(
                    {
                        "source_type": "donnees",
                        "citation": _citation(serie, r.langue).strip("[]"),
                        "source_url": serie.page_source,
                        "source_date": (
                            (
                                f"نُشرت في {formater_date(derniere_publication, 'ar')}"
                                if r.langue == "ar"
                                else f"Publié le {formater_date(derniere_publication, 'fr')}"
                            )
                            if derniere_publication else ""
                        ),
                        "score": 1.0,
                        "excerpt": "",
                    }
                )
            series_data.append(
                {
                    "code": serie.code,
                    "label": serie.libelle_ar if r.langue == "ar" else serie.libelle_fr,
                    "unit": serie.unite,
                    "points": [
                        {"date": item.date_valeur.isoformat(), "value": item.valeur}
                        for item in observations[-POINTS_MAX:]
                    ],
                    "facts": faits,
                    "source_url": serie.page_source,
                    "published_at": (
                        derniere_publication.isoformat() if derniere_publication else None
                    ),
                    "test_data": serie.est_test,
                }
            )

    if any(serie.est_test for serie in demande.series):
        # Le prototype tourne sur des séries inventées : aucune réponse ne doit
        # pouvoir être prise pour un chiffre officiel.
        r.paragraphes.append(
            "_بيانات تجريبية وهمية: هذه القيم ليست صادرة عن البنك المركزي الموريتاني._"
            if r.langue == "ar"
            else "_Données fictives de démonstration : ces valeurs ne sont pas publiées par la BCM._"
        )

    return ReponseDonnees(
        answer="\n\n".join(r.paragraphes),
        grounded=une_valeur,
        sources=sources,
        data={
            "operation": demande.operation,
            "period": {
                "start": demande.periode.debut.isoformat() if demande.periode.debut else None,
                "end": demande.periode.fin.isoformat(),
            },
            "series": series_data,
        },
        nombres_autorises=r.autorises,
    )
