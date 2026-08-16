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

- les pages rendues ne quittent jamais la machine ;
- le résultat OCR n’est transmis à aucun fournisseur de génération ;
- les caches se trouvent dans `storage/chart_pages/` et sont exclus du dépôt Git ;
- si le rendu ou l’OCR échoue, l’API revient à la réponse RAG textuelle habituelle.

## Fichiers ajoutés ou modifiés

- `api/charts.py` : intention, sélection, rendu, OCR, cadrage et explication ;
- `scripts/chart_ocr.swift` : reconnaissance locale Apple Vision ;
- `scripts/build_chart_ocr.sh` : compilation automatique ;
- `api/app.py` : branche graphique de l’API ;
- `frontend/app.py` : indicateur local et exemples ;
- `tests/test_charts.py` : tests unitaires du traitement graphique.

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
- la lecture est optimisée pour macOS, car elle utilise Apple Vision ;
- une validation métier reste nécessaire avant une mise en production publique.

## Vérification

Commande :

```bash
APP_ENV=test GENERATION_PROVIDER=extractive .venv/bin/python -m pytest -q
```

Résultat actuel : **36 tests réussis**.
