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
