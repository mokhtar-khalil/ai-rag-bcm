# Phase 3 — Analyse locale des graphiques

## Objectif

Permettre à l’assistant de retrouver et d’expliquer un graphique du rapport BCM sans dépendre uniquement du texte natif du PDF. La réponse reste limitée au rapport, cite la page PDF et ne reconstitue aucune valeur illisible.

## Chaîne de traitement

1. **Détection de l’intention** — les mots « graphique », « courbe », « figure », « diagramme » ou « histogramme » activent la voie graphique. Les formulations combinant une mesure et une périodicité, par exemple « volume des virements par mois », l’activent aussi.
2. **Recherche thématique élargie** — l’index hybride examine jusqu’à 30 passages. Les termes métier relient notamment « intermédiation » à « coefficient de transformation ».
3. **Sélection de page** — un numéro explicite, comme « graphique 23 », est prioritaire. Sinon, le sujet de la question pèse davantage que la simple présence du mot « graphique ».
4. **Rendu PDF local** — `pdftoppm` transforme uniquement les pages retenues en PNG à 170 dpi.
5. **OCR local** — un petit exécutable Swift utilise Apple Vision pour détecter les libellés et leurs coordonnées.
6. **Cadrage** — le titre le plus proche de la question détermine la colonne et la zone verticale du graphique. Cela évite de mélanger plusieurs figures d’une même page.
7. **Lecture structurée** — les années, séries et valeurs sont reliées par leur position. Pour le graphique dépôts/crédits/intermédiation, les valeurs 2024 et 2025 sont regroupées par série et les variations sont calculées.
8. **Explication prudente** — la réponse combine la lecture visuelle et les phrases pertinentes de la même page, avec citations `[p. PDF N]`.
9. **Affichage Gradio** — l’interface indique que l’analyse graphique est locale et précise les pages analysées.

## Confidentialité

Avec le moteur par défaut `CHART_ANALYSIS_BACKEND=local_ocr` :

- les pages rendues ne quittent jamais la machine ;
- le résultat OCR n’est transmis à aucun fournisseur de génération ;
- les caches se trouvent dans `storage/chart_pages/` et sont exclus du dépôt Git ;
- si le rendu ou l’OCR échoue, l’API revient à la réponse RAG textuelle habituelle.

Avec `gemini_vision`, l’image de la page **est envoyée à Google** (voir ci-dessous).

## Lecture visuelle Gemini — serveur Linux

L’OCR Apple Vision n’existe que sous macOS : en production (Docker, Railway), la
voie graphique était donc désactivée. Le moteur `gemini_vision` la rend
disponible sur n’importe quel serveur.

### Réglages

| Variable | Défaut | Rôle |
|---|---|---|
| `CHART_ANALYSIS_ENABLED` | `true` en local, `false` dans l’image Docker | active la voie graphique |
| `CHART_ANALYSIS_BACKEND` | `local_ocr` (image Docker : `gemini_vision`) | `local_ocr` ou `gemini_vision` |
| `CHART_VISION_MODEL` | `gemini-3.6-flash` | modèle qui lit l’image |
| `CHART_VISION_MAX_OUTPUT_TOKENS` | `6000` | plafond de sortie, réflexion comprise |
| `GEMINI_API_KEY` | — | requis ; la configuration refuse de démarrer en production sans elle |

Le niveau de réflexion suit `GEMINI_THINKING_LEVEL` (`low` par défaut).

### Chaîne de traitement

1. Étapes 1 à 4 inchangées : intention, recherche, sélection d’au plus
   `CHART_MAX_PAGES` pages, rendu PNG par `pdftoppm` (installé dans l’image).
2. Chaque PNG est envoyé à Gemini avec la question, en parallèle
   (`extract_chart_contexts_gemini` dans `api/charts.py`). Le modèle renvoie un
   JSON par graphique : numéro, titre, type, axes et unités, période, séries et
   valeurs, tendance principale, éléments illisibles. Une valeur imprimée est
   recopiée ; une valeur seulement lisible sur un axe gradué est une estimation
   arrondie (2 à 3 chiffres significatifs), affichée « ≈ … (estimation
   visuelle) » et présentée comme approximative dans la réponse ; sans
   graduation, elle est marquée illisible.
2 bis. Seuls les graphiques qui correspondent à la question sont gardés : le
   numéro cité par l’utilisateur fait foi ; sinon, le titre, les axes et les
   séries doivent couvrir au moins 60 % des termes de la question (3 au plus),
   et seule la figure la plus proche est retenue parmi les pages lues. Si aucune
   ne correspond, la réponse est rédigée à partir du texte, sans
   `chart_analysis`.
3. Le JSON est mis en forme en un bloc « Lecture visuelle de la page PDF N »
   placé **en tête des preuves** du générateur habituel (OpenAI ou Gemini).
   L’explication par règles `explain_chart_locally` n’est pas utilisée : la
   réponse est rédigée par le modèle de génération, citée `[p. PDF N]`, et la
   réponse API porte `chart_analysis: true` et `chart_pages`.
4. La lecture est mise en cache à côté du PNG
   (`storage/chart_pages/<empreinte>-page-N.<clé>.vision.json`). La clé combine
   la version des consignes, le modèle et la question normalisée (casse, accents
   et ponctuation ignorés) : une question répétée ne coûte plus rien. Seule une
   lecture valide est mise en cache.
5. Les appels sont journalisés dans la consommation du pilote sous l’opération
   `chart_vision`.
6. Une erreur 5xx de Gemini (surcharge, fréquente : 2 sur 8 appels lors des
   essais) est retentée une fois après 1,5 s. En cas d’erreur persistante (clé
   absente, quota, délai de 45 s, JSON invalide, sortie tronquée), l’API journalise `chart_analysis_failed backend=gemini_vision` et répond par
   la voie documentaire habituelle, à partir du texte du rapport. Si une page
   sur deux échoue, l’autre est conservée.

### Coût et latence mesurés

Mesures du 21/10/2026 en local, `gemini-3.6-flash`, rédaction par OpenAI, sur
les questions des graphiques 23, 76, 82 et dépôts/crédits (8 lectures de page) :

- entrée : ≈ 1 650 à 1 800 tokens par page (double page à 170 dpi + consignes) ;
- sortie : ≈ 950 à 2 750 tokens de JSON (une page de quatre graphiques coûte le
  plus), plus 0 à 850 tokens de réflexion ;
- au tarif public d’un Gemini Flash de la génération 3 (≈ 0,50 $ par million de
  tokens en entrée, ≈ 3 $ en sortie — à vérifier sur la grille Google en
  vigueur), soit **≈ 0,004 à 0,011 $ par page**, **≈ 0,01 à 0,02 $ par question**
  (deux pages lues). Le bloc ajouté au générateur est marginal ;
- latence : **6 à 16 s par lecture** ; les deux pages sont lues en parallèle,
  donc **+8 à 16 s par question** au premier passage. Question complète
  (planification, lecture, rédaction OpenAI) : **25 à 36 s**. Une lecture en
  cache ne coûte que quelques millisecondes.

### Fiabilité des valeurs estimées — à valider avant production

Comparées à une lecture humaine du graphique 76 (virements ACH mensuels, barres
sans étiquettes), les estimations visuelles ne sont **pas encore fiables** :

- une lecture a donné ≈ 127 000 en janvier pour une barre réelle d’environ 5 000 ;
- une autre, après consigne d’arrondi, situe bien le début et la fin d’année,
  mais s’écarte de 30 à 40 % sur juin, juillet, septembre et novembre.

Les valeurs imprimées (titres, légendes, tendances, chiffres du texte) sont en
revanche bien restituées. Piste à tester : envoyer l’image en
`media_resolution` haute ou découper la double page, le graphique n’occupant
qu’une petite partie des ≈ 1 100 tokens d’image par défaut. L’essai n’a pas pu
être mené : la clé locale, sur l’offre gratuite de Gemini, a atteint son quota
journalier (`429 RESOURCE_EXHAUSTED`).

**Offre gratuite à proscrire en production** : quota de requêtes très bas, et
les données envoyées peuvent servir à Google pour améliorer ses produits. Une
clé d’un projet facturé est requise.

Le cache vit dans le conteneur : sur Railway sans volume, il repart à vide à
chaque redéploiement.

### Confidentialité

Les images des pages du rapport (document public) sont envoyées à Google au
moment de la question. La question de l’utilisateur l’accompagne. Ce choix doit
être validé par la BCM avant d’activer `CHART_ANALYSIS_ENABLED=true` en
production.

## Fichiers ajoutés ou modifiés

- `api/charts.py` : intention, sélection, rendu, OCR, cadrage et explication ;
- `scripts/chart_ocr.swift` : reconnaissance locale Apple Vision ;
- `scripts/build_chart_ocr.sh` : compilation automatique ;
- `api/app.py` : branche graphique de l’API ;
- `frontend/app.py` : indicateur local et exemples ;
- `tests/test_charts.py` : tests unitaires du traitement graphique ;
- `tests/test_chart_vision.py` : lecture visuelle Gemini (client simulé, aucun appel réseau).

## Exemples validés

### Graphique 23 — liquidité bancaire

La page PDF 26 est sélectionnée par son numéro. Le titre « Évolution de la liquidité bancaire depuis 2022 » est reconnu et l’explication s’appuie sur le commentaire de la même page, notamment le niveau élevé de liquidité et l’évolution des réserves.

### Dépôts, crédits et intermédiation

La page PDF 42 est sélectionnée. Le système lit notamment :

- dépôts : 134,1 milliards de MRU en 2024 et 158,7 en 2025 ;
- crédits : 108,2 milliards de MRU en 2024 et 122,8 en 2025 ;
- intermédiation : 81 % en 2024 et 77 % en 2025.

Il en déduit que les dépôts ont progressé plus vite que les crédits, ce qui explique le recul de quatre points du taux d’intermédiation.

### Graphique 76 — virements ACH mensuels

La question « explique le volume des virements par mois en 2025 » sélectionne directement la page PDF 65. Le système isole les douze barres bleues, estime leur hauteur à partir de l’espacement régulier des graduations de l’axe et décrit les principaux mouvements : accélération à partir de juin, niveaux élevés en juillet et octobre, recul en novembre et maximum en décembre.

L’échelle est reconstruite à partir de plusieurs graduations afin qu’une erreur OCR isolée, par exemple une confusion entre 158 875 et 758 875, ne déforme pas toute l’analyse.

### Graphique 82 — achats et ventes de devises

La formulation « explique l’évolution des achats et ventes de devises » sélectionne directement le graphique 82 de la page PDF 67, et non le graphique 32 qui traite uniquement des interventions de vente de la BCM.

La réponse combine le graphique et les totaux annuels précis du paragraphe de la même page : achats USD +94 %, achats EUR +183 %, ventes USD -50 % et ventes EUR -29 %. Une question comme « y a-t-il un graphique… ? » commence explicitement par « Oui », puis indique le numéro, le titre et la page. Si aucune figure correspondante n’est retrouvée, elle commence par « Non ».

## Limites actuelles

- l’OCR peut confondre une virgule et un point ; la réponse normalise l’affichage français mais ne devine pas une valeur absente ;
- un graphique très dense, manuscrit ou de faible résolution peut nécessiter une formulation contenant son numéro ou son titre ;
- l’OCR local est réservé à macOS, car il utilise Apple Vision ; sur Linux, seule la lecture visuelle Gemini est disponible ;
- avec `gemini_vision`, le suivi « résume cette section » sur une page scannée n’a pas d’équivalent : l’OCR documentaire reste propre à `local_ocr` ;
- une validation métier reste nécessaire avant une mise en production publique.

## Vérification

Commande :

```bash
APP_ENV=test GENERATION_PROVIDER=extractive .venv/bin/python -m pytest -q
```

Résultat actuel : **36 tests réussis**.
