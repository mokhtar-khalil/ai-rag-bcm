"""Voie des données chiffrées : détection, dates, requêtes, gabarits et API."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from api.app import create_app
from api.donnees import (
    FREQUENCE_EVENEMENT,
    FREQUENCE_QUOTIDIENNE,
    AmbiguiteDonnees,
    DemandeDonnees,
    Observation,
    Serie,
    charger_catalogue,
    creer_schema,
    detecter_demande,
    inserer_observations,
    inserer_serie,
    nombres_cites,
    nombres_non_justifies,
    repondre,
    resoudre_periode,
)
from core.config import get_settings


# Vendredi : la valeur du jour n'est pas encore publiée, celle de la veille l'est.
REFERENCE = date(2026, 10, 23)

EUR = Serie(
    code="EUR", libelle_fr="cours de référence de l'euro",
    libelle_ar="سعر الصرف المرجعي لليورو", court_fr="Cours EUR/MRU",
    court_ar="سعر صرف اليورو", unite="MRU", unite_ar="أوقية جديدة", decimales=2,
    frequence=FREQUENCE_QUOTIDIENNE, page_source="https://www.bcm.mr/",
    synonymes=("euro", "euros", "eur", "€", "يورو"),
)
USD = Serie(
    code="USD", libelle_fr="cours de référence du dollar américain",
    libelle_ar="سعر الصرف المرجعي للدولار الأمريكي", court_fr="Cours USD/MRU",
    court_ar="سعر صرف الدولار", unite="MRU", unite_ar="أوقية جديدة", decimales=2,
    frequence=FREQUENCE_QUOTIDIENNE, page_source="https://www.bcm.mr/",
    synonymes=("dollar", "dollars", "usd", "دولار"),
)
TAUX = Serie(
    code="TAUX_DIRECTEUR", libelle_fr="taux directeur de la BCM",
    libelle_ar="سعر الفائدة الرئيسي للبنك المركزي الموريتاني",
    court_fr="Taux directeur", court_ar="سعر الفائدة الرئيسي", unite="%", unite_ar="%",
    decimales=2, frequence=FREQUENCE_EVENEMENT, page_source="https://www.bcm.mr/",
    synonymes=("taux directeur", "سعر الفائدة الرئيسي"),
)
CATALOGUE = (EUR, USD, TAUX)


def _obs(jour: str, valeur: float, publie: str | None = None) -> Observation:
    return Observation(
        date.fromisoformat(jour), valeur, date.fromisoformat(publie or jour)
    )


@pytest.fixture
def base(tmp_path: Path) -> Path:
    """Petite base aux valeurs connues, dont une correction publiée après coup."""
    chemin = tmp_path / "donnees.sqlite"
    with sqlite3.connect(chemin) as conn:
        creer_schema(conn)
        for serie in CATALOGUE:
            inserer_serie(conn, serie)
        inserer_observations(
            conn,
            "EUR",
            [
                _obs("2026-10-16", 43.10),
                _obs("2026-10-19", 43.25),
                _obs("2026-10-20", 43.40),
                # La BCM corrige le cours du 20 le lendemain : la correction fait foi.
                _obs("2026-10-20", 43.45, publie="2026-10-21"),
                _obs("2026-10-21", 43.05),
                _obs("2026-10-22", 43.12),
            ],
        )
        inserer_observations(
            conn, "USD", [_obs("2026-10-01", 39.90), _obs("2026-10-22", 39.50)]
        )
        inserer_observations(
            conn, "TAUX_DIRECTEUR", [_obs("2025-02-03", 6.50), _obs("2026-06-01", 6.00)]
        )
    return chemin


def _demande(question: str, langue: str = "fr") -> DemandeDonnees:
    demande = detecter_demande(question, CATALOGUE, REFERENCE, langue)
    assert isinstance(demande, DemandeDonnees), question
    return demande


# ---- Détection ----------------------------------------------------------------


def test_une_question_de_cours_est_reconnue() -> None:
    demande = _demande("Quel est le cours de l'euro aujourd'hui ?")
    assert [serie.code for serie in demande.series] == ["EUR"]
    assert demande.operation == "valeur"
    assert demande.periode.fin == REFERENCE
    assert demande.periode.explicite is False


@pytest.mark.parametrize(
    "question",
    [
        "Quelles réformes concernent la zone euro ?",
        "Pourquoi le taux directeur a-t-il baissé en 2026 ?",
        "Que dit le rapport annuel sur le cours du dollar ?",
        "Quel est le taux d'inflation en 2025 ?",
        "ما سبب تغير سعر صرف اليورو؟",
    ],
)
def test_les_questions_documentaires_restent_au_rag(question: str) -> None:
    assert detecter_demande(question, CATALOGUE, REFERENCE) is None


def test_une_question_vague_demande_une_precision() -> None:
    resultat = detecter_demande("Quel est le taux actuel ?", CATALOGUE, REFERENCE)
    assert isinstance(resultat, AmbiguiteDonnees)


def test_plusieurs_series_dans_une_question() -> None:
    demande = _demande("Quel était le cours de l'euro et du dollar hier ?")
    assert {serie.code for serie in demande.series} == {"EUR", "USD"}


def test_question_arabe_reconnue() -> None:
    demande = _demande("ما هو سعر صرف اليورو أمس؟", "ar")
    assert [serie.code for serie in demande.series] == ["EUR"]
    assert demande.periode.fin == date(2026, 10, 22)


# ---- Dates ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "debut", "fin"),
    [
        ("cours de l'euro hier", None, "2026-10-22"),
        ("cours de l'euro avant-hier", None, "2026-10-21"),
        ("cours de l'euro le 22/10/2026", None, "2026-10-22"),
        ("cours de l'euro le 1er octobre 2026", None, "2026-10-01"),
        ("cours de l'euro fin 2025", None, "2025-12-31"),
        ("cours de l'euro depuis janvier", "2026-01-01", "2026-10-23"),
        ("cours de l'euro en novembre", "2025-11-01", "2025-11-30"),
        ("cours de l'euro le mois dernier", "2026-09-01", "2026-09-30"),
        ("cours de l'euro en 2025", "2025-01-01", "2025-12-31"),
        (
            "cours de l'euro entre le 1er octobre 2026 et le 20 octobre 2026",
            "2026-10-01",
            "2026-10-20",
        ),
    ],
)
def test_resolution_des_dates(question: str, debut: str | None, fin: str) -> None:
    periode = resoudre_periode(question, REFERENCE)
    assert periode.debut == (date.fromisoformat(debut) if debut else None)
    assert periode.fin == date.fromisoformat(fin)


def test_une_evolution_sans_periode_porte_sur_trente_jours() -> None:
    demande = _demande("Comment évolue le cours de l'euro ?")
    assert demande.operation == "evolution"
    assert demande.periode.debut == date(2026, 9, 23)


def test_une_periode_ne_depasse_pas_aujourd_hui() -> None:
    demande = _demande("Cours de l'euro en 2026 ?")
    assert demande.periode.fin == REFERENCE


# ---- Réponses -----------------------------------------------------------------


def test_valeur_du_jour_demande(base: Path) -> None:
    reponse = repondre(_demande("Cours de l'euro hier ?"), base, "fr", REFERENCE)
    assert reponse.grounded is True
    assert reponse.answer == (
        "Au 22 octobre 2026, le cours de référence de l'euro est de **43,12 MRU** "
        "[Données BCM · Cours EUR/MRU]."
    )
    assert reponse.sources[0]["source_type"] == "donnees"
    assert reponse.sources[0]["source_url"] == "https://www.bcm.mr/"
    assert reponse.data["series"][0]["points"] == [{"date": "2026-10-22", "value": 43.12}]


def test_sans_date_on_donne_la_derniere_valeur_publiee(base: Path) -> None:
    reponse = repondre(
        _demande("Quel est le cours de l'euro aujourd'hui ?"), base, "fr", REFERENCE
    )
    assert reponse.answer.startswith("Dernière valeur publiée : au 22 octobre 2026")


def test_un_dimanche_renvoie_la_valeur_du_vendredi_en_le_disant(base: Path) -> None:
    reponse = repondre(
        _demande("Cours de l'euro le 18 octobre 2026 ?"), base, "fr", REFERENCE
    )
    assert "Aucune valeur n'est publiée pour le dimanche 18 octobre 2026" in reponse.answer
    assert "vendredi 16 octobre 2026" in reponse.answer
    assert "**43,10 MRU**" in reponse.answer


def test_la_valeur_corrigee_fait_foi(base: Path) -> None:
    reponse = repondre(
        _demande("Cours de l'euro le 20 octobre 2026 ?"), base, "fr", REFERENCE
    )
    assert "**43,45 MRU**" in reponse.answer
    assert "21 octobre 2026" in reponse.sources[0]["source_date"]


def test_evolution_calculee_par_le_backend(base: Path) -> None:
    demande = _demande(
        "Comment le cours de l'euro a-t-il évolué entre le 16 octobre 2026 "
        "et le 22 octobre 2026 ?"
    )
    reponse = repondre(demande, base, "fr", REFERENCE)
    faits = reponse.data["series"][0]["facts"]
    assert faits["nombre"] == 5
    assert faits["plus_haut"] == {"date": "2026-10-20", "valeur": 43.45}
    assert faits["plus_bas"] == {"date": "2026-10-21", "valeur": 43.05}
    assert "est passé de 43,10 à **43,12 MRU**" in reponse.answer
    assert "soit une variation de +0,02 MRU (+0,0 %)" in reponse.answer
    assert "- Moyenne : 43,19 MRU sur 5 valeurs publiées" in reponse.answer


def test_taux_directeur_en_vigueur(base: Path) -> None:
    reponse = repondre(_demande("Quel est le taux directeur ?"), base, "fr", REFERENCE)
    assert reponse.answer == (
        "Au 23 octobre 2026, le taux directeur de la BCM est de **6,00 %**, en vigueur "
        "depuis le 1er juin 2026 [Données BCM · Taux directeur]."
    )


def test_date_hors_historique_refusee(base: Path) -> None:
    reponse = repondre(
        _demande("Cours du dollar le 1er janvier 2020 ?"), base, "fr", REFERENCE
    )
    assert reponse.grounded is False
    assert reponse.sources == []
    assert "Aucune valeur du cours de référence du dollar américain" in reponse.answer
    assert "L'historique disponible commence le 1er octobre 2026." in reponse.answer


def test_reponse_arabe(base: Path) -> None:
    reponse = repondre(_demande("ما هو سعر صرف اليورو أمس؟", "ar"), base, "ar", REFERENCE)
    assert "22 أكتوبر 2026" in reponse.answer
    assert "**43,12 أوقية جديدة**" in reponse.answer
    assert "[بيانات البنك المركزي · سعر صرف اليورو]" in reponse.answer


def test_les_gabarits_ne_citent_que_des_nombres_calcules(base: Path) -> None:
    questions = [
        "Cours de l'euro hier ?",
        "Cours de l'euro le 18 octobre 2026 ?",
        "Comment évolue le cours de l'euro depuis le 1er octobre 2026 ?",
        "Quel est le taux directeur ?",
        "Cours de l'euro et du dollar hier ?",
    ]
    for question in questions:
        reponse = repondre(_demande(question), base, "fr", REFERENCE)
        assert nombres_non_justifies(reponse.answer, reponse.nombres_autorises) == [], question


def test_un_nombre_invente_est_detecte() -> None:
    texte = "Le cours est de 43,12 MRU, en hausse de 5 % depuis le 1er octobre."
    assert nombres_cites(texte) == {"43,12", "5", "1"}
    assert nombres_non_justifies(texte, {"43,12", "1"}) == ["5"]


def test_mention_des_donnees_fictives(tmp_path: Path) -> None:
    from scripts.creer_base_donnees_test import remplir

    chemin = tmp_path / "demo.sqlite"
    assert remplir(chemin, date(2026, 10, 1), REFERENCE) > 0
    catalogue = charger_catalogue(chemin)
    assert {serie.code for serie in catalogue} == {"EUR", "USD", "CNY", "TAUX_DIRECTEUR"}
    assert all(serie.est_test for serie in catalogue)
    demande = detecter_demande("Cours de l'euro hier ?", catalogue, REFERENCE)
    assert isinstance(demande, DemandeDonnees)
    reponse = repondre(demande, chemin, "fr", REFERENCE)
    assert reponse.answer.endswith(
        "_Données fictives de démonstration : ces valeurs ne sont pas publiées par la BCM._"
    )
    assert reponse.data["series"][0]["test_data"] is True


# ---- API ----------------------------------------------------------------------


def _client(base: Path, enabled: bool = True):
    settings = get_settings(
        {
            "APP_ENV": "test",
            "GENERATION_PROVIDER": "extractive",
            "SEMANTIC_RETRIEVAL": "false",
            "OPEN_BROWSER": "false",
            "DATA_TOOLS_ENABLED": "true" if enabled else "false",
            "DATA_DB_PATH": str(base),
        }
    )
    return create_app(settings_override=settings).test_client()


def test_api_sert_la_question_chiffree_sans_modele(base: Path, monkeypatch) -> None:
    monkeypatch.setattr("api.app.aujourd_hui", lambda: REFERENCE)
    monkeypatch.setattr(
        "api.app.stream_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Une question chiffrée ne doit pas atteindre le générateur.")
        ),
    )
    response = _client(base).post(
        "/api/ask", json={"question": "Quel était le cours de l'euro hier ?"}
    )
    assert response.status_code == 200
    assert response.json["grounded"] is True
    assert "**43,12 MRU**" in response.json["answer"]
    assert response.json["sources"][0]["source_type"] == "donnees"
    assert response.json["data"]["operation"] == "valeur"


def test_api_diffuse_l_etape_donnees(base: Path, monkeypatch) -> None:
    monkeypatch.setattr("api.app.aujourd_hui", lambda: REFERENCE)
    response = _client(base).post(
        "/api/ask/stream", json={"question": "Quel est le taux directeur ?"}
    )
    corps = response.get_data(as_text=True)
    assert 'event: stage\ndata: {"stage": "donnees"}' in corps
    assert "6,00 %" in corps


def test_api_question_vague_propose_des_series(base: Path) -> None:
    response = _client(base).post("/api/ask", json={"question": "Quel est le taux actuel ?"})
    assert response.json["clarification_needed"] is True
    assert "Quel est le taux directeur de la BCM ?" in response.json["suggestions"]


def test_api_voie_desactivee_par_defaut(base: Path) -> None:
    response = _client(base, enabled=False).post(
        "/api/ask", json={"question": "Quel était le cours de l'euro hier ?"}
    )
    assert response.status_code == 200
    assert "data" not in response.json


def test_api_question_documentaire_non_interceptee(base: Path, monkeypatch) -> None:
    monkeypatch.setattr("api.app.aujourd_hui", lambda: REFERENCE)
    response = _client(base).post(
        "/api/ask",
        json={"question": "Quel a été le taux de croissance du PIB réel en 2025 ?"},
    )
    assert response.status_code == 200
    assert "data" not in response.json
