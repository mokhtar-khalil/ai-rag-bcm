from PIL import Image, ImageDraw

from api.charts import (
    _document_ocr_context,
    enrich_chart_query,
    explain_chart_locally,
    explicit_chart_numbers,
    financial_statement_kind,
    is_chart_question,
    is_chart_existence_question,
    select_chart_pages,
)


def test_document_ocr_preserves_two_column_reading_order() -> None:
    text = _document_ocr_context(
        119,
        [
            {"x": 0.55, "y": 0.80, "width": 0.30, "text": "Opinion de l'auditeur"},
            {"x": 0.05, "y": 0.70, "width": 0.30, "text": "Responsabilité de la direction"},
            {"x": 0.55, "y": 0.60, "width": 0.30, "text": "Audit selon les normes ISA"},
            {"x": 0.05, "y": 0.90, "width": 0.30, "text": "Rapport indépendant"},
            {"x": 0.05, "y": 0.02, "width": 0.20, "text": "Rapport annuel 2025"},
        ],
    )
    assert text.index("Rapport indépendant") < text.index("Responsabilité")
    assert text.index("Responsabilité") < text.index("Opinion de l'auditeur")
    assert text.index("Opinion de l'auditeur") < text.index("normes ISA")
    assert "Rapport annuel 2025" not in text


def test_chart_intent_and_query_enrichment() -> None:
    question = "Explique le graphique sur les dépôts et les crédits."
    enriched = enrich_chart_query(question)
    assert is_chart_question(question) is True
    assert "coefficient transformation" in enriched
    assert is_chart_question("Quel est le montant des dépôts ?") is False
    assert is_chart_question("Explique le volume des virements par mois en 2025") is True
    assert (
        is_chart_question("Explique l'évolution des achats et ventes de devises")
        is True
    )
    assert is_chart_existence_question("Y a-t-il un graphique sur les devises ?") is True
    assert is_chart_question("Explique l'organigramme de la BCM") is True
    assert "Gouverneurs Adjoints" in enrich_chart_query(
        "Explique l'organigramme de la BCM"
    )
    arabic_question = "اشرح حجم التحويلات شهرياً خلال عام 2025"
    assert is_chart_question(arabic_question) is True
    assert "ACH" in enrich_chart_query(arabic_question)


def test_organization_chart_reconstructs_visible_hierarchy() -> None:
    context = {
        "pdf_page": 80,
        "text": "\n".join(
            [
                "[OCR local de la page PDF 80; coordonnées normalisées x/y]",
                "Utiliser les positions seulement pour relier les libellés.",
                "- x=0.215 y=0.756 : Organigramme",
                "- x=0.054 y=0.696 : Conseil Général",
                "- x=0.158 y=0.700 : Conseil Politique Monétaire",
                "- x=0.212 y=0.552 : Le Gouverneur",
                "- x=0.212 y=0.472 : (2) Gouverneurs Adjoints",
                "- x=0.073 y=0.540 : Comité de Direction",
                "- x=0.125 y=0.413 : Secrétaire Général",
                "- x=0.105 y=0.294 : Direction Générale de l'Administration",
                "- x=0.302 y=0.300 : Direction Générale de la Supervision Bancaire et de la Stabilité Financière",
                "- x=0.307 y=0.061 : Directions Régionales",
            ]
        ),
    }
    answer = explain_chart_locally(
        "Explique l'organigramme de la BCM",
        [context],
        [],
    )
    assert answer is not None
    assert answer.startswith("L’organigramme de la BCM place le Gouverneur au centre")
    assert "quatre conseils" in answer
    assert "deux Gouverneurs adjoints" in answer
    assert "neuf directions générales" in answer
    assert "sigles UMEF et PAFHD" in answer
    assert "[p. PDF 80]" in answer


def test_financial_position_table_is_selected_and_analyzed() -> None:
    question = "Analyse l'état de la situation financière"
    search_results = [
        {
            "pdf_page": 106,
            "score": 0.8,
            "text": "Analyse des variables financières et note souveraine.",
        },
        {
            "pdf_page": 96,
            "score": 0.2,
            "text": "14 — ETATS FINANCIERS I — Etat de la situation financière Chiffres en MRU",
        },
    ]
    assert is_chart_question(question) is True
    assert is_chart_question("analyse l'tat de la situation financière") is True
    assert select_chart_pages(question, search_results, maximum=2) == [96]

    context = {
        "pdf_page": 96,
        "text": "\n".join(
            [
                "[OCR local de la page PDF 96; coordonnées normalisées x/y]",
                "- x=0.525 y=0.918 : I - Etat de la situation financière",
                "- x=0.531 y=0.544 : Total des actifs",
                "- x=0.824 y=0.542 : 131 274 381 414",
                "- x=0.917 y=0.542 : 124 719 755 331",
                "- x=0.539 y=0.760 : Trésorerie et dépôts en monnaie étrangère",
                "- x=0.828 y=0.760 : 39 350 867 767",
                "- x=0.922 y=0.760 : 44 820 105 876",
                "- x=0.539 y=0.721 : Placements en monnaie étrangère",
                "- x=0.830 y=0.721 : 48 926 212 063",
                "- x=0.922 y=0.721 : 34 423 308 595",
                "- x=0.539 y=0.661 : Créances sur l'État au coût amorti",
                "- x=0.828 y=0.661 : 25 876 633 112",
                "- x=0.922 y=0.661 : 31 279 779 880",
                "- x=0.529 y=0.366 : Total des passifs",
                "- x=0.824 y=0.366 : 115 980 766 382",
                "- x=0.917 y=0.366 : 112 183 193 085",
                "- x=0.538 y=0.503 : Billets et monnaies en circulation",
                "- x=0.827 y=0.503 : 34 467 622 139",
                "- x=0.922 y=0.503 : 31 286 117 472",
                "- x=0.538 y=0.482 : Comptes courants et dépôts au coût amorti",
                "- x=0.826 y=0.482 : 27 697 808 997",
                "- x=0.922 y=0.482 : 32 141 134 350",
                "- x=0.538 y=0.462 : Engagements liés à la Politique Monétaire",
                "- x=0.826 y=0.462 : 19 020 000 000",
                "- x=0.921 y=0.464 : 16 119 992 891",
            ]
        ),
    }
    answer = explain_chart_locally(question, [context], [])
    assert answer is not None
    assert answer.startswith("L’état de la situation financière compare le bilan")
    assert "131,27 milliards MRU" in answer
    assert "+5,3 %" in answer
    assert "placements en monnaie étrangère" in answer
    assert "15,29 milliards MRU" in answer
    assert "11,7 %" in answer
    assert "ratio prudentiel de solvabilité" in answer
    assert "[p. PDF 96]" in answer


def test_all_financial_statement_titles_are_recognized() -> None:
    assert financial_statement_kind("Analyse l'état de la situation financière") == "position"
    assert (
        financial_statement_kind(
            "Analyser l'État du résultat net et des autres éléments du résultat global"
        )
        == "comprehensive_income"
    )
    assert (
        financial_statement_kind("Analyse l'état des variations des capitaux propres")
        == "equity_changes"
    )
    assert financial_statement_kind("Analyse l'état des flux de trésorerie") == "cash_flows"


def test_comprehensive_income_statement_is_analyzed() -> None:
    question = "Analyser l'État du résultat net et des autres éléments du résultat global"
    results = [
        {
            "pdf_page": 97,
            "score": 0.5,
            "text": (
                "II — État du résultat net et des autres éléments du résultat global "
                "Chiffres en MRU III — État des variations des capitaux propres "
                "Chiffres en MRU"
            ),
        }
    ]
    assert select_chart_pages(question, results, maximum=2) == [97]
    context = {
        "pdf_page": 97,
        "text": "\n".join(
            [
                "[OCR local de la page PDF 97; coordonnées normalisées x/y]",
                "- x=0.025 y=0.918 : II - État du résultat net et des autres éléments du résultat global",
                "- x=0.036 y=0.425 : Résultat net de l'exercice",
                "- x=0.333 y=0.425 : 3 182 791 988",
                "- x=0.423 y=0.425 : 2 614 765 287",
                "- x=0.038 y=0.550 : Produit net bancaire après coût du risque",
                "- x=0.333 y=0.550 : 5 447 435 347",
                "- x=0.424 y=0.550 : 3 925 344 978",
                "- x=0.036 y=0.236 : Résultat global de l'exercice",
                "- x=0.333 y=0.235 : 4 048 874 109",
                "- x=0.424 y=0.235 : 3 134 901 297",
            ]
        ),
    }
    answer = explain_chart_locally(question, [context], [])
    assert answer is not None
    assert "3,18 milliards MRU" in answer
    assert "+21,7 %" in answer
    assert "4,05 milliards MRU" in answer
    assert "[p. PDF 97]" in answer


def test_explicit_chart_number_selects_only_its_page() -> None:
    question = "Explique le graphique 23 sur la liquidité bancaire."
    results = [
        {
            "pdf_page": 42,
            "score": 0.8,
            "text": "Graphique 57: Évolution du ratio de liquidité.",
        },
        {
            "pdf_page": 26,
            "score": 0.2,
            "text": "Graphique 23: Évolution de la liquidité bancaire depuis 2022.",
        },
    ]
    assert explicit_chart_numbers(question) == {23}
    assert select_chart_pages(question, results, maximum=2) == [26]


def test_local_explanation_reconstructs_deposit_credit_comparison() -> None:
    context = {
        "pdf_page": 42,
        "text": "\n".join(
            [
                "[OCR local de la page PDF 42; coordonnées normalisées x/y]",
                "Utiliser les positions seulement pour relier titres, séries, années et valeurs.",
                "- x=0.592 y=0.279 : Évolution des dépôts, des crédits et de l'intermédiation (en mds de MRU et en %)",
                "- x=0.804 y=0.072 : 2024",
                "- x=0.868 y=0.072 : 2025",
                "- x=0.795 y=0.170 : 134.1",
                "- x=0.814 y=0.144 : 108.2",
                "- x=0.810 y=0.187 : 81",
                "- x=0.858 y=0.205 : 158 7",
                "- x=0.876 y=0.152 : 122,8",
                "- x=0.875 y=0.170 : 77",
            ]
        ),
    }
    answer = explain_chart_locally(
        "Que montre le graphique sur les dépôts, crédits et l’intermédiation ?",
        [context],
        [],
    )
    assert answer is not None
    assert "134,1 à 158,7" in answer
    assert "108,2 à 122,8" in answer
    assert "81 % à 77 %" in answer
    assert "[p. PDF 42]" in answer


def test_monthly_bar_chart_is_read_from_local_pixels(tmp_path) -> None:
    image_path = tmp_path / "monthly.png"
    image = Image.new("RGB", (1000, 500), "white")
    draw = ImageDraw.Draw(image)
    baseline = 430
    heights = [20, 15, 18, 16, 45, 90, 150, 135, 140, 210, 145, 250]
    for index, height in enumerate(heights):
        left = 130 + index * 62
        draw.rectangle(
            (left, baseline - height, left + 38, baseline),
            fill=(83, 111, 198),
        )
    image.save(image_path)
    context = {
        "pdf_page": 65,
        "image_path": str(image_path),
        "text": "\n".join(
            [
                "[OCR local de la page PDF 65; coordonnées normalisées x/y]",
                "Utiliser les positions seulement pour relier titres, séries, années et valeurs.",
                "- x=0.100 y=0.900 : Graphique 76:",
                "- x=0.100 y=0.870 : ACH, Virements volume et valeur par mois en 2025",
                "- x=0.110 y=0.200 : 10000",
                "- x=0.110 y=0.400 : 20000",
                "- x=0.110 y=0.600 : 30000",
                "- x=0.110 y=0.800 : 40000",
            ]
        ),
    }
    answer = explain_chart_locally(
        "Explique le volume des virements par mois en 2025",
        [context],
        [],
    )
    assert answer is not None
    assert "janvier" in answer
    assert "décembre" in answer
    assert "Le maximum se situe en décembre" in answer

    arabic_answer = explain_chart_locally(
        "اشرح حجم التحويلات شهرياً خلال عام 2025",
        [context],
        [],
    )
    assert arabic_answer is not None
    assert "يناير" in arabic_answer
    assert "ديسمبر" in arabic_answer
    assert "أعلى حجم" in arabic_answer
    assert "janvier" not in arabic_answer

    selected_arabic_answer = explain_chart_locally(
        "Explique le volume des virements par mois en 2025",
        [context],
        [],
        language="ar",
    )
    assert selected_arabic_answer is not None
    assert "يناير" in selected_arabic_answer
    assert "ديسمبر" in selected_arabic_answer
    assert "janvier" not in selected_arabic_answer

    distractor = {
        **context,
        "pdf_page": 43,
        "text": context["text"].replace(
            "Graphique 76:", "Graphique 50:"
        ).replace(
            "ACH, Virements volume et valeur par mois en 2025",
            "Wallets bancaires, volume des transactions par mois en 2025",
        ),
    }
    selected_page_answer = explain_chart_locally(
        "اشرح حجم التحويلات شهرياً خلال سنة 2025",
        [distractor, context],
        [],
        language="ar",
    )
    assert selected_page_answer is not None
    assert "[p. PDF 65]" in selected_page_answer
    assert "[p. PDF 43]" not in selected_page_answer


def test_currency_chart_answers_yes_and_uses_precise_page_evidence() -> None:
    context = {
        "pdf_page": 67,
        "text": "\n".join(
            [
                "[OCR local de la page PDF 67; coordonnées normalisées x/y]",
                "Utiliser les positions seulement pour relier titres, séries, années et valeurs.",
                "- x=0.528 y=0.772 : Graphique 82:",
                "- x=0.528 y=0.760 : Évolution des achats et ventes de devises Euro et USD (2024-2025)",
                "- x=0.606 y=0.575 : T1-24",
                "- x=0.917 y=0.370 : T4-25",
            ]
        ),
    }
    evidence = [
        {
            "pdf_page": 67,
            "text": (
                "Les achats en USD ont atteint 623 105 USD, contre 320 548 USD "
                "en 2024, soit une hausse de 94%. Les achats en EUR se sont élevés "
                "à 2,97 millions EUR, contre 1,05 millions EUR l’année précédente, "
                "en progression de 183%. Concernant les ventes, elles se sont établies "
                "à 3,74 millions USD contre 7,41 millions USD en 2024, en baisse de "
                "50%, et à 4,27 millions EUR contre 6,01 millions EUR un an plus tôt, "
                "soit un recul de 29%."
            ),
        }
    ]
    answer = explain_chart_locally(
        "Y a-t-il un graphique sur les achats et ventes de devises euro ou dollar ?",
        [context],
        evidence,
    )
    assert answer is not None
    assert answer.startswith("Oui. Le rapport contient le graphique 82")
    assert "page PDF 67" in answer
    assert "320 548 USD en 2024 contre 623 105 USD en 2025" in answer
    assert "7,41 millions USD en 2024 contre 3,74 millions USD" in answer
