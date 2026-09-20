"""Préparation déterministe des requêtes avant leur recherche dans l'index."""

from __future__ import annotations

import re
import unicodedata

from core.language import is_arabic_text


INTERNATIONAL_MARKERS = {
    "international", "mondial", "monde", "états-unis", "usa", "chine", "japon", "europe",
    "zone euro", "france", "allemagne", "espagne", "italie", "royaume-uni", "brésil", "inde",
}

# Ce glossaire ne fabrique aucune réponse. Il ajoute uniquement les termes métier
# employés dans le rapport lorsque l'utilisateur choisit un synonyme courant.
QUERY_EXPANSIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("انظمة الدفع", "أنظمة الدفع", "نظم الدفع", "وسائل الدفع"),
     "réformes systèmes et moyens de paiement modernisation infrastructure paiements numériques interopérabilité compensation règlement"),
    (("التضخم", "ارتفاع الاسعار", "ارتفاع الأسعار"),
     "inflation indice national des prix à la consommation INPC"),
    (("الناتج المحلي", "النمو الاقتصادي"),
     "croissance produit intérieur brut réel PIB réel activité économique"),
    (("السيولة المصرفية", "سيولة البنوك"),
     "liquidité bancaire réserves libres réserves obligatoires open market"),
    (("الحساب الجاري", "الميزان الجاري", "عجز الحساب"),
     "compte courant balance des paiements déficit courant transactions courantes "
     "pourcentage du PIB"),
    (("الميزان التجاري", "الصادرات", "الواردات"),
     "balance commerciale exportations importations échanges extérieurs"),
    (("الاحتياطيات", "احتياطي العملات"),
     "réserves officielles brutes avoirs extérieurs mois importations"),
    # « الدين » n'avait aucune entrée : une question arabe sur la dette n'était
    # enrichie que par « الناتج المحلي », qui ajoute le vocabulaire de la
    # croissance et emmène la recherche vers les mauvaises pages.
    (("الدين", "المديونية", "الديون", "الدين الخارجي", "الدين العام"),
     "dette extérieure encours de la dette dette publique service de la dette "
     "pourcentage du PIB part de la dette endettement"),
    (("خدمة الدين",),
     "service de la dette remboursements intérêts échéances"),
    (("الودائع",),
     "dépôts clientèle secteur bancaire ressources banques"),
    (("القروض", "الائتمان"),
     "crédits créances secteur bancaire crédit à l'économie"),
    # « القروض » seul menait au volume des crédits, jamais à leur qualité :
    # une question sur les impayés retombait sur le total des actifs.
    (("القروض المتعثرة", "المتعثرة", "التعثر", "القروض غير المنتظمة",
      "جودة المحفظة", "المخصصات"),
     "prêts non performants taux de sinistralité créances en souffrance "
     "qualité du portefeuille provisions couverture des créances douteuses"),
    (("الملاءة", "كفاية رأس المال", "الأموال الذاتية"),
     "ratio de solvabilité fonds propres réglementaires adéquation des fonds propres"),
    (("المجاميع النقدية", "الكتلة النقدية"),
     "agrégats monétaires masse monétaire monnaie au sens large"),
    (("التحويلات", "الحوالات"),
     "virements transferts ACH systèmes de paiement volume valeur"),
    # Le rapport dit « wallet bancaire » là où les utilisateurs disent
    # « application » ou « app ». Sans ce pont, la page qui détaille les huit
    # acteurs du marché n'est jamais retrouvée : « application bancaire »
    # n'apparaît nulle part dans le corpus.
    (("application bancaire", "applications bancaires", "app bancaire",
      "apps bancaires", "appli bancaire", "applis bancaires",
      "application mobile", "applications mobiles", "application de paiement",
      # « wallet » est le terme du rapport lui-même : l'employer doit ramener le
      # vocabulaire de la page qui compare les acteurs du marché.
      "wallet", "wallets", "portefeuille electronique", "porte-monnaie electronique"),
     "wallets bancaires wallet bancaire monnaie électronique mobile banking "
     "portefeuille électronique transactions volumes montants répartition par "
     "opérateur concentration du marché leader position quasi-systémique"),
    # Les marques citées dans le rapport : les nommer doit suffire à retrouver
    # la page qui les compare. Le passage qui chiffre réellement les parts de
    # marché (« un leader (Bankily) en position quasi-systémique, avec environ
    # 73,5% des montants ») est distinct de celui qui nomme les huit wallets :
    # ses termes propres doivent être présents dans l'expansion, sinon la
    # recherche s'arrête au premier passage qui se contente de les lister.
    (("bankily", "masrvi", "sedad", "bim-bank", "bimbank", "bci-pay", "bcipay",
      "amanty", "bamis-digital", "bamis digital", "part de marche", "parts de marche"),
     "wallets bancaires actifs sur le marché parts de marché volumes montants "
     "transactions monnaie électronique écosystème répartition par opérateur "
     "concentration du marché leader position quasi-systémique asymétrie "
     "challengers peloton d'acteurs"),
    (("agregats monetaires", "aggregate monetaire"),
     "masse monétaire monnaie au sens large billets en circulation dépôts à vue dépôts à terme actifs extérieurs nets actifs intérieurs nets"),
    (("activite economique", "progresse en volume", "progression en volume"),
     "croissance du produit intérieur brut réel PIB réel"),
    (("coussin en devises", "couvrir ses achats exterieurs", "couverture exterieure"),
     "réserves officielles brutes mois d'importations hors industries extractives"),
    (("prix a la consommation", "rythme des prix", "hausse des prix"),
     "inflation indice national des prix à la consommation INPC"),
    (("encours monetaire", "forme de liquidite", "masse monetaire"),
     "composition masse monétaire dépôts à vue encours total"),
    (("stock de titres publics", "titres publics mauritaniens"),
     "encours global valeurs du Trésor"),
    (("fonds propres reglementaires", "couvraient les risques bancaires"),
     "ratio moyen de solvabilité des banques"),
    (("bilan agrege", "taille du bilan", "taille des etablissements bancaires"),
     "total des actifs secteur bancaire"),
    (("credits presentait des difficultes", "difficultes de remboursement"),
     "taux de sinistralité créances en souffrance prêts non performants"),
    (("echanges courants avec l'exterieur", "echanges courants exterieur"),
     "déficit du compte courant balance des paiements"),
    (("premier envoi de gnl", "premier gaz"),
     "exportations gaz naturel liquéfié projet Grand Tortue Ahmeyim"),
    (("demarche environnementale et sociale", "responsabilite environnementale et sociale"),
     "stratégie ESG RSE"),
    (("liquidite bancaire", "liquidite des banques"),
     "conditions de liquidité du système bancaire actifs liquides coefficient de liquidité moyen"),
)


# Extrait de « LISTE DES ABRÉVIATIONS » (Rapport annuel 2025, p. 12) : un
# sigle officiel du rapport résolu vers son développé, et réciproquement.
# Contrairement au glossaire colloquial ci-dessus, cette table est fermée et
# entièrement dérivée du document — aucune supposition sur la façon dont un
# utilisateur formule sa question. Elle corrige un angle mort précis : « PIB »
# n'a par exemple aucune chance de croiser lexicalement le passage qui donne
# son montant, rédigé en toutes lettres (« produit intérieur brut »). « PDF »
# est exclu : il entre en collision avec le format de citation interne
# (« p. PDF N ») et ne désigne ici aucune notion utile aux questions posées.
REPORT_ACRONYMS: dict[str, str] = {
    "ACH": "Chambre de compensation automatique",
    "AFD": "Agence Française de Développement",
    "AFI": "Alliance pour l’Inclusion Financière",
    "AFRITAC": "Africa Technical Assistance Center",
    "ANSADE": "Agence Nationale de la Statistique et de l’Analyse Démographique",
    "APAM": "Association des Professionnels d’Assurance de Mauritanie",
    "ATS": "Automated Transfer System",
    "BAD": "Banque Africaine de Développement",
    "BCE": "Banque Centrale Européenne",
    "BCK": "Banque Centrale de Koweït",
    "BCM": "Banque Centrale de Mauritanie",
    "BICR": "Bureau d’Informations sur le Crédit",
    "BID": "Banque Islamique de Développement",
    "BIT": "Bon Islamique de Trésor",
    "BM": "Banque Mondiale",
    "BoE": "Banque d’Angleterre",
    "BoJ": "Banque du Japon",
    "BSA": "Bank Supervision Application",
    "CAF": "Coût, Assurance et Fret",
    "CAPEC": "Caisses populaires d’Épargne et de Crédit",
    "CBDC": "Monnaie numérique de banque centrale",
    "CBS": "Core Banking System",
    "CEEM": "Centre d’Études en Économie Monétaire",
    "CPM": "Conseil de Politique Monétaire",
    "CPRSF": "Comité de Prévention des Risques Systémiques et de la Stabilité Financière",
    "CSD": "Central Securities Depository",
    "CSPCT": "Conseil des Systèmes de Paiement, de Compensation et de Règlement des Titres",
    "DA": "Dinar Arabe",
    "DGD": "Direction Générale des Douanes",
    "DGSBSF": "Direction Générale de la Supervision Bancaire et de la Stabilité Financière",
    "DTS": "Droits de Tirage Spéciaux",
    "eKYC": "Electronic Know Your Customer, ou identification électronique du client",
    "EMEs": "Établissements de Monnaie Électronique",
    "ESG": "Environnement, Social et Gouvernance",
    "ESRS": "European Sustainability Reporting Standards",
    "FAD": "Fonds Africain de Développement",
    "FADEA": "Fonds d’Abu Dhabi pour le Développement Économique Arabe",
    "FADES": "Fonds Arabe pour le Développement Économique et Social",
    "FEC": "Facilité élargie de crédit",
    "FED": "Réserve Fédérale des États-Unis",
    "FIDA": "Fonds International de Développement Agricole",
    "FKDEA": "Fonds Koweïtien pour Développement Économique Arabe",
    "FMA": "Fonds monétaire arabe",
    "FMI": "Fonds monétaire international",
    "FNRH": "Fonds National des Revenus des Hydrocarbures",
    "FOB": "Free On Board",
    "FPSO": "Floating Production Storage and Offloading",
    "FRD": "Fond de Résilience et Durabilité",
    "FTSE": "Footsie",
    "FVI": "Finance Verte Inclusive",
    "GAFI": "Groupe d’Action Financière",
    "GES": "Gaz à Effet de Serre",
    "GIMTEL": "Groupement Interbancaire de Monétique et des Transactions Électroniques",
    "GTA": "Grand Tortue Ahmeyim",
    "HTM": "Held-To-Maturity (Titres détenus jusqu’à échéance)",
    "HTCS": "Held-To-Collect-and-Sell (Titres détenus pour collecte et vente)",
    "IA": "Intelligence artificielle",
    "IAS": "International Accounting Standards",
    "IDA": "Association Internationale de Développement",
    "IDE": "Investissements directs étrangers",
    "IFRS": "International Financial Reporting Standards",
    "IMF": "Institution de Microfinance",
    "LBC-FT": "Lutte contre le blanchiment de capitaux et le financement du terrorisme",
    "LCR": "Liquidité à court terme",
    "LFI": "Loi de Finances Initiale",
    "LPRC": "Loan Prime Rate",
    "MA": "Ministère de l’Agriculture",
    "MCM": "Société des Mines de Cuivre de Mauritanie",
    "MDRI": "Multilateral Debt Relief Initiative",
    "ME": "Ministère de l’Élevage",
    "MENAFAT": "Groupe d’Action Financière Moyen-Orient et Afrique du Nord",
    "MF": "Ministère des Finances",
    "MIB": "Marché Interbancaire de Change",
    "MPMEs": "Micro, petites et moyennes entreprises",
    "NEET": "Not in Employment, Éducation or Training",
    "MRU": "Ouguiya, monnaie nationale de la Mauritanie",
    "NSFR": "Liquidité à court terme et à long terme",
    "ODD": "Objectifs de Développement Durable",
    "OPEP": "Organisation des Pays Exportateurs de Pétrole",
    "PAMIF": "Projet d’Appui à la Modernisation de l’Infrastructure Financière",
    "PAFIID": "Programme d’Appui à la Finance Innovante, Inclusive et Durable",
    "PIB": "Produit Intérieur Brut",
    "PROCAPEC": "Agence Nationale de Promotion des Caisses Populaires de Crédit et d’Épargne",
    "RTGS": "Système de règlement brut en temps réel",
    "ROA": "Rendement des Actifs (Return on Assets)",
    "ROE": "Rendement des Capitaux Propres (Return on Equity)",
    "RSE": "Responsabilité Sociale des Entreprises",
    "SAR": "Riyal Saoudien",
    "SASB": "Sustainability Accounting Standards Board",
    "SCAPP": "Stratégie de Croissance Accélérée et de Prospérité Partagée",
    "SFI": "Société Financière Internationale",
    "SMCP": "Société Mauritanienne de Commercialisation de Poissons",
    "SNEF": "Stratégie Nationale d’Éducation Financière",
    "SNIF": "Stratégie Nationale d’Inclusion Financière",
    "SNIM": "Société Nationale Industrielle et Minière",
    "SNP": "Système National de Paiement",
    "SNPN": "Stratégie Nationale de Paiement Numérique",
    "SOMELEC": "Société Mauritanienne de l’Électricité",
    "TASIAST": "Société des Mines de Tasiast",
    "TVA": "Taxe sur la valeur ajoutée",
    "USD": "Dollar américain",
}

# Sigles de deux lettres exclus du sens sigle → développé : « MA », « ME »,
# « DA », « IA », « BM », « MF » croisent des mots français courants
# (« ma », « me », « da »...). La correspondance sur le sigle exige déjà la
# casse exacte et une frontière de mot, ce qui écarte l'essentiel du risque,
# mais un sigle de deux lettres reste trop court pour l'exclure entièrement à
# la casse près. Le sens développé → sigle reste actif pour ces entrées : une
# question qui nomme « Banque Mondiale » en toutes lettres profite quand même
# de l'ajout de « BM ».
_MIN_ACRONYM_LENGTH_FOR_FORWARD_MATCH = 3


def _expand_report_acronyms(question: str) -> list[str]:
    """Ajoute le développé d'un sigle du rapport, ou l'inverse, sans appel LLM."""
    additions: list[str] = []
    folded_question = _fold(question)
    for acronym, expansion in REPORT_ACRONYMS.items():
        if len(acronym) >= _MIN_ACRONYM_LENGTH_FOR_FORWARD_MATCH and re.search(
            rf"\b{re.escape(acronym)}\b", question
        ):
            additions.append(expansion)
        elif _fold(expansion) in folded_question:
            additions.append(acronym)
    return additions


def _fold(text: str) -> str:
    """Produit une version sans accents ni distinction de casse."""
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def is_national_question(question: str) -> bool:
    """Indique si la question vise la Mauritanie plutôt qu'un contexte international."""
    lowered = question.casefold()
    return not any(marker in lowered for marker in INTERNATIONAL_MARKERS)


def build_retrieval_query(question: str) -> str:
    """Enrichit une question avec un glossaire métier sans appel génératif."""
    normalized = re.sub(r"\s+", " ", question).strip()
    folded = _fold(normalized)
    additions = [
        expansion
        for triggers, expansion in QUERY_EXPANSIONS
        if any(trigger in folded for trigger in triggers)
    ]
    additions.extend(_expand_report_acronyms(normalized))
    # Un même mot peut désigner plusieurs périmètres dans un rapport bancaire.
    # L'expansion conserve volontairement les deux familles de sens ; le système
    # demandera ensuite une clarification au lieu d'en privilégier une en dur.
    precise_deposit_scope = any(
        marker in folded
        for marker in (
            "clientele",
            "etablissements financiers",
            "comptes courants",
            "aupres de la bcm",
            "banque centrale",
        )
    )
    if (
        "depot" in folded
        and any(marker in folded for marker in ("aupres de la bcm", "banque centrale"))
    ):
        additions.append(
            "comptes courants et dépôts au coût amorti banques et établissements financiers"
        )
    if (
        "depot" in folded
        and any(marker in folded for marker in ("banque", "bancair"))
        and not precise_deposit_scope
    ):
        additions.append(
            "dépôts de la clientèle dépôts bancaires collecte ressources des banques "
            "comptes courants et dépôts au coût amorti banques et établissements financiers"
        )
    if is_national_question(normalized) and not is_arabic_text(normalized):
        additions.append("Mauritanie")
    return " ".join([normalized, *additions])
