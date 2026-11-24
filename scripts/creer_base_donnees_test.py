"""Crée une base de séries FICTIVES pour essayer la voie des données chiffrées.

En attendant l'accès aux données de la BCM, ce script remplit la base avec des
cours et un taux directeur inventés, marqués `est_test`. Chaque réponse servie à
partir de ces séries porte une mention « Données fictives de démonstration ».

    python scripts/creer_base_donnees_test.py
    DATA_TOOLS_ENABLED=true ./run.sh

Les valeurs sont calculées par une formule fixe, sans tirage aléatoire : deux
exécutions produisent la même base, ce qui rend les démonstrations répétables.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.donnees import (  # noqa: E402
    FREQUENCE_EVENEMENT,
    FREQUENCE_QUOTIDIENNE,
    Observation,
    Serie,
    aujourd_hui,
    creer_schema,
    inserer_observations,
    inserer_serie,
)
from core.config import get_settings  # noqa: E402


PAGE_SITE = "https://www.bcm.mr/"

SERIES_TEST = (
    (
        Serie(
            code="EUR", libelle_fr="cours de référence de l'euro",
            libelle_ar="سعر الصرف المرجعي لليورو", court_fr="Cours EUR/MRU",
            court_ar="سعر صرف اليورو", unite="MRU", unite_ar="أوقية جديدة",
            decimales=2, frequence=FREQUENCE_QUOTIDIENNE, page_source=PAGE_SITE,
            synonymes=("euro", "euros", "eur", "€", "يورو"), est_test=True,
        ),
        43.20, 1.10,
    ),
    (
        Serie(
            code="USD", libelle_fr="cours de référence du dollar américain",
            libelle_ar="سعر الصرف المرجعي للدولار الأمريكي", court_fr="Cours USD/MRU",
            court_ar="سعر صرف الدولار", unite="MRU", unite_ar="أوقية جديدة",
            decimales=2, frequence=FREQUENCE_QUOTIDIENNE, page_source=PAGE_SITE,
            synonymes=("dollar", "dollars", "usd", "$", "دولار"), est_test=True,
        ),
        39.80, 0.70,
    ),
    (
        Serie(
            code="CNY", libelle_fr="cours de référence du yuan",
            libelle_ar="سعر الصرف المرجعي لليوان", court_fr="Cours CNY/MRU",
            court_ar="سعر صرف اليوان", unite="MRU", unite_ar="أوقية جديدة",
            decimales=3, frequence=FREQUENCE_QUOTIDIENNE, page_source=PAGE_SITE,
            synonymes=("yuan", "renminbi", "cny", "يوان"), est_test=True,
        ),
        5.520, 0.090,
    ),
)

TAUX_DIRECTEUR = Serie(
    code="TAUX_DIRECTEUR", libelle_fr="taux directeur de la BCM",
    libelle_ar="سعر الفائدة الرئيسي للبنك المركزي الموريتاني",
    court_fr="Taux directeur", court_ar="سعر الفائدة الرئيسي", unite="%",
    unite_ar="%", decimales=2, frequence=FREQUENCE_EVENEMENT, page_source=PAGE_SITE,
    synonymes=("taux directeur", "taux d'interet directeur", "سعر الفائدة الرئيسي"),
    est_test=True,
)
# Décisions inventées : date d'effet et taux en vigueur.
DECISIONS_TEST = ((date(2024, 3, 1), 7.00), (date(2025, 2, 3), 6.50), (date(2026, 6, 1), 6.00))


def jours_ouvres(debut: date, fin: date) -> list[date]:
    """Jours du lundi au vendredi : aucun cours n'est publié le week-end."""
    jours = []
    jour = debut
    while jour <= fin:
        if jour.weekday() < 5:
            jours.append(jour)
        jour += timedelta(days=1)
    return jours


def cours_fictif(base: float, amplitude: float, rang: int, decimales: int) -> float:
    """Oscillation lente et déterministe autour d'un niveau de base."""
    valeur = base + amplitude * math.sin(rang / 37) + amplitude * 0.3 * math.sin(rang / 5)
    return round(valeur, decimales)


def remplir(path: Path, debut: date, fin: date) -> int:
    """Recrée la base et renvoie le nombre de valeurs insérées."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    total = 0
    with sqlite3.connect(path) as conn:
        creer_schema(conn)
        jours = jours_ouvres(debut, fin)
        for serie, base, amplitude in SERIES_TEST:
            inserer_serie(conn, serie)
            total += inserer_observations(
                conn,
                serie.code,
                (
                    Observation(jour, cours_fictif(base, amplitude, rang, serie.decimales), jour)
                    for rang, jour in enumerate(jours)
                ),
            )
        inserer_serie(conn, TAUX_DIRECTEUR)
        total += inserer_observations(
            conn,
            TAUX_DIRECTEUR.code,
            (Observation(jour, taux, jour) for jour, taux in DECISIONS_TEST if jour <= fin),
        )
        conn.execute(
            "INSERT INTO synchronisations VALUES (?, ?, ?, 0, '')",
            (fin.isoformat(), "scripts/creer_base_donnees_test.py", total),
        )
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chemin", type=Path, default=get_settings().data_db_path)
    parser.add_argument("--debut", type=date.fromisoformat, default=date(2025, 1, 2))
    parser.add_argument("--fin", type=date.fromisoformat, default=aujourd_hui())
    arguments = parser.parse_args()
    total = remplir(arguments.chemin, arguments.debut, arguments.fin)
    print(f"{total} valeurs fictives écrites dans {arguments.chemin}")


if __name__ == "__main__":
    main()
