# Phase 2 — Qualité du retrieval et précision des réponses

## Objectif

Cette phase améliore la capacité du chatbot à retrouver la bonne information lorsque la question ne reprend pas exactement les mots du rapport. Le périmètre reste volontairement limité au rapport annuel BCM : aucun système multi-document n'est ajouté.

La règle de travail est mesurable : chaque changement est comparé au moteur de la phase 1 sur un même jeu de questions. Une modification n'est conservée que si elle améliore le rappel des pages pertinentes tout en maintenant les refus hors rapport.

## 1. Jeu d'évaluation

Le fichier `evaluation/questions.jsonl` contient 41 cas :

- 35 questions dont la réponse figure dans le rapport ;
- 6 questions volontairement absentes ;
- des questions directes ;
- des reformulations utilisant un vocabulaire différent ;
- des comparaisons et questions de raisonnement ;
- des demandes de listes.

Chaque question présente possède une ou plusieurs pages PDF attendues. Certains cas contiennent aussi les éléments numériques qui devront apparaître dans la réponse. Les questions absentes ont une liste de pages vide.

Ce jeu constitue une première référence technique. Avant une validation métier définitive, un expert BCM devra confirmer les pages attendues et ajouter les formulations réellement observées auprès des utilisateurs.

## 2. Métriques

Le script `scripts/evaluate_retrieval.py` calcule :

- `Hit@1` : une page attendue est le premier résultat ;
- `Hit@3` et `Hit@5` : une page attendue figure dans les trois ou cinq premiers résultats ;
- `Hit@12` : une page attendue figure parmi les candidats transmis au reranker ;
- `MRR` : moyenne de l'inverse du rang de la première bonne page ;
- `answerable_acceptance` : le garde-fou accepte les questions réellement documentées ;
- `grounded_hit_at_5` : une page attendue est dans le top 5 et le garde-fou accepte la question ;
- `refusal_accuracy` : les questions absentes sont bien refusées.

## 3. Mesure de référence

Le moteur lexical de la phase 1 combinait TF-IDF mots et caractères. Ses résultats sur les 40 cas étaient :

| Mesure | Phase 1 lexicale |
|---|---:|
| Hit@1 | 55,88 % |
| Hit@3 | 67,65 % |
| Hit@5 | 70,59 % |
| MRR | 64,33 % |
| Reformulations réussies | 27,27 % |
| Refus hors rapport | 66,67 % |

Le diagnostic est clair : les questions directes fonctionnaient déjà bien, mais le vocabulaire reformulé faisait chuter fortement le retrieval. Deux questions absentes étaient également acceptées à tort.

Le rapport complet de cette référence est conservé dans `evaluation/results/baseline_lexical.json`.

## 4. Embeddings sémantiques locaux

Chaque passage est maintenant représenté par un vecteur sémantique de 384 dimensions. Le modèle multilingue `intfloat/multilingual-e5-small` est exécuté localement avec Sentence Transformers. Sa carte officielle indique qu'il est conçu pour la recherche multilingue et qu'il utilise des préfixes différenciant requêtes et passages.

- les passages sont encodés avec le préfixe `passage: ` ;
- les questions sont encodées avec le préfixe `query: ` ;
- les vecteurs sont normalisés pour utiliser une similarité cosinus ;
- les 2 413 vecteurs sont enregistrés avec l'index persistant ;
- les poids publics du modèle sont placés dans `storage/models/`.

Le téléchargement initial représente environ 470 Mio. Une fois le modèle présent, le chargement est forcé en mode local hors ligne : le PDF et les questions ne sont pas envoyés au dépôt du modèle.

Les embeddings sont adaptés à la recherche sémantique. L'envoi des passages du rapport à une API d'embeddings externe n'a cependant pas été retenu, car une clé disponible ne constitue pas une autorisation d'export du document. L'indexation sémantique est donc entièrement locale.

Sources techniques : [modèle multilingue E5](https://huggingface.co/intfloat/multilingual-e5-small) et [concept d'embeddings pour la recherche](https://developers.openai.com/api/docs/models/text-embedding-3-small).

## 5. Recherche hybride

Trois signaux sont utilisés :

1. TF-IDF mots et bigrammes, représentant 78 % du score lexical ;
2. TF-IDF caractères, représentant 22 % du score lexical ;
3. similarité sémantique locale.

Les scores TF-IDF et cosinus n'ont pas la même échelle. Ils ne sont donc pas additionnés directement pour le classement. Le moteur utilise Reciprocal Rank Fusion :

```text
score_RRF = 0,45 / (60 + rang_lexical) + 0,55 / (60 + rang_sémantique)
```

Le sémantique apporte la compréhension des reformulations. Le signal lexical reste conservé séparément pour protéger les recherches portant sur des nombres, sigles, noms propres et libellés exacts. Pour une comparaison datée, la meilleure ligne de tableau lexicale obtient une place réservée parmi les candidats : elle ne peut plus être éliminée uniquement parce que son style télégraphique est moins bien compris par les embeddings.

Les pages liminaires et tables des matières restent pénalisées. La diversification autorise deux passages provenant de la même page et, si nécessaire, une ligne de tableau supplémentaire.

## 6. Expansion de requête explicable

`api/query.py` contient un petit glossaire métier. Il ne produit aucune réponse et n'appelle aucun modèle génératif. Il traduit seulement certaines formulations utilisateur vers le vocabulaire exact du rapport.

Exemples :

| Formulation utilisateur | Termes ajoutés pour le retrieval |
|---|---|
| « coussin en devises » | réserves officielles brutes, mois d'importations |
| « bilan agrégé des banques » | total des actifs du secteur bancaire |
| « montant des dépôts dans les banques » | dépôts de la clientèle, comptes courants et dépôts, banques et établissements financiers |
| « crédits en difficulté » | taux de sinistralité, créances en souffrance |
| « stock de titres publics » | encours global des valeurs du Trésor |
| « activité en volume » | croissance du PIB réel |

Ce glossaire est versionné, testable et contrôlable. Pour une formulation ambiguë, il ajoute volontairement les deux familles de sens au lieu de choisir une réponse en dur.

## 7. Reformulation multiple et fusion des recherches

Lorsque la question est courte, faiblement appariée ou ambiguë, un planificateur produit jusqu'à quatre formulations autonomes. Seule la question utilisateur lui est transmise : aucun extrait du rapport n'est utilisé à cette étape.

Chaque formulation déclenche ensuite une recherche **locale** dans le même index. Les listes obtenues sont fusionnées par Reciprocal Rank Fusion. Le nombre final de candidats reste limité à 12 ; la reformulation n'augmente donc pas le volume de passages transmis au reranker.

Cette étape n'est pas une mémoire de questions-réponses. Elle sert à couvrir plusieurs expressions possibles d'une même intention, puis à laisser les preuves du rapport déterminer la suite.

## 8. Garde-fou de pertinence

Les similarités du modèle local sont relativement élevées, même pour une question éloignée. Le seuil initial de `0,32` acceptait donc toutes les questions absentes.

Le seuil a été calibré à `0,88` avec une double preuve :

- preuve lexicale : score lexical suffisant et au moins deux termes utiles communs ; ou
- preuve sémantique : similarité supérieure au seuil avec au moins deux termes communs, ou similarité exceptionnellement forte supérieure à `0,92`.

Ce contrôle a rétabli 100 % des refus sur le jeu hors rapport sans rejeter les pages pertinentes après expansion de requête.

## 9. Reranking et contexte de réponse

L'API récupère 12 candidats. Lorsque le moteur génératif distant configuré est disponible, le reranker peut désormais conserver jusqu'à huit passages réellement utiles au lieu de cinq. Cela permet aux réponses de type « quelles mesures » ou « pourquoi » de couvrir plusieurs éléments du rapport.

Les instructions de rédaction imposent maintenant :

- 2 à 4 phrases utiles pour un fait simple ;
- une conclusion directe puis 3 à 7 puces pour une analyse ;
- la comparaison des valeurs, de l'écart et du sens de l'évolution ;
- l'exploitation de tous les passages utiles ;
- l'interdiction de réduire une réponse disponible à un extrait isolé.

La limite de sortie a été relevée de 1 000 à 1 400 jetons pour autoriser ces réponses plus substantielles.

## 10. Clarification générique des questions ambiguës

La formulation « montant de dépôt dans les banques, comparaison 2024-2025 » peut raisonnablement désigner deux notions présentes dans le rapport :

- **dépôts de la clientèle du secteur bancaire** : tableau 6, page PDF 33 ;
- **dépôts des banques auprès de la BCM** : note 12, page PDF 113.

Le système ne mémorise ni la question ni sa réponse. Il procède ainsi :

1. le planificateur crée des reformulations couvrant les sens possibles ;
2. chaque reformulation interroge l'index local ;
3. les libellés suggérés sont extraits des passages réellement retrouvés ;
4. si au moins deux périmètres sont confirmés par le rapport, l'API renvoie `clarification_needed: true` et les formulations proposées ;
5. l'utilisateur sélectionne une formulation dans Gradio ; cette question précise repart ensuite dans le pipeline normal.

Une suggestion inventée par le planificateur mais absente des passages locaux est rejetée. Dans ce cas concret, l'interface propose notamment :

- « Comparer l'indicateur “Dépôts de la clientèle” entre 2024 et 2025 » ;
- « Comparer l'indicateur “Banques et établissements financiers” entre 2024 et 2025 ».

Après confirmation du premier périmètre, l'interpréteur générique de tableaux lit 134,0 et 158,7 milliards de MRU et calcule la hausse de 24,7 milliards, soit 18,4 %. Après confirmation du second, il lit les valeurs distinctes de la note 12. Aucun de ces nombres n'est enregistré dans le code.

## 11. Indexation des tableaux et comparaison chiffrée

En plus des passages textuels, l'index contient maintenant des micro-passages correspondant aux lignes numériques. Chaque ligne conserve :

- l'en-tête de colonnes et son ordre exact ;
- le libellé, y compris lorsqu'il est réparti sur deux lignes ;
- les valeurs et l'unité ;
- la page PDF d'origine.

L'interpréteur de tableaux repère les deux années demandées, associe les valeurs selon l'ordre des colonnes, puis calcule l'écart absolu et relatif. Cette logique est générique : les tests l'appliquent aussi bien aux dépôts qu'aux crédits et ne contiennent aucune réponse BCM codée en dur.

## 12. Mode extractif corrigé

Le mode de secours profite lui aussi du glossaire. Un test bout en bout avait révélé une confusion entre :

- croissance totale du PIB réel : 4,0 % ;
- croissance de l'activité non extractive : 5,1 %.

Le classement des phrases utilise maintenant les termes enrichis et pénalise un sous-périmètre comme « non extractif » lorsqu'il n'est pas demandé. La reformulation « progression de l'activité en volume » retourne donc correctement 4,0 %, avec les valeurs de 2024 et 2023.

## 13. Résultats finaux

| Mesure | Phase 1 lexicale | Phase 2 hybride | Évolution |
|---|---:|---:|---:|
| Hit@1 | 55,88 % | 65,71 % | +9,83 points |
| Hit@3 | 67,65 % | 85,71 % | +18,06 points |
| Hit@5 | 70,59 % | 97,14 % | +26,55 points |
| Hit@12 | non mesuré | 100 % | tous les cas présents arrivent au reranker |
| MRR | 64,33 % | 78,36 % | +14,03 points |
| Reformulations réussies | 27,27 % | 90,91 % | +63,64 points |
| Questions directes | 94,44 % | 100 % | +5,56 points |
| Raisonnement | 66,67 % | 100 % | +33,33 points |
| Question ambiguë ajoutée | non mesuré | 100 % | page pertinente dans les candidats |
| Refus hors rapport | 66,67 % | 100 % | +33,33 points |

Le seul cas situé hors du top 5 reste dans les 12 candidats envoyés au reranker. Le rapport final est `evaluation/results/hybrid_v6.json`.

La référence lexicale historique contient 40 cas ; la version hybride v6 en contient 41 après ajout du test ambigu sur les dépôts. Les écarts restent donc indicatifs, tandis que les pourcentages de la colonne Phase 2 sont ceux du benchmark courant. La suite automatisée comporte 30 tests, tous réussis, dont les régressions sur la liquidité bancaire et la confirmation d'un indicateur.

## 14. Temps d'exécution

- construction lexicale des 2 413 passages : environ 4,4 secondes sur la machine de développement ;
- chargement du modèle et indexation sémantique : environ 25 secondes ;
- première question après démarrage : environ 4 à 6 secondes pour charger le modèle local ;
- questions suivantes en mode extractif local : environ 50 à 80 millisecondes sur les cas vérifiés ;
- appels de génération et de reranking : variables selon le service configuré.

Le modèle est maintenant préchargé par `GET /health`. L'attente intervient donc au démarrage contrôlé par `run.sh`, et non lors de la première question utilisateur.

## 15. Commandes utiles

Construire ou actualiser les deux index :

```bash
.venv/bin/python scripts/index_report.py
.venv/bin/python scripts/index_embeddings.py
```

Rejouer l'évaluation :

```bash
.venv/bin/python scripts/evaluate_retrieval.py \
  --mode hybrid \
  --output evaluation/results/hybrid_v6.json
```

Exécuter les tests sans appel externe :

```bash
APP_ENV=test GENERATION_PROVIDER=extractive \
  .venv/bin/python -m pytest -q
```

## 16. Limites restantes

- 41 cas constituent une bonne base de développement mais pas encore une homologation métier ;
- la qualité finale de la rédaction doit être évaluée séparément du retrieval ;
- les tableaux PDF complexes peuvent encore être mal linéarisés par `pypdf` ;
- le modèle local consomme de la mémoire, raison pour laquelle Gunicorn utilise un worker par défaut ;
- les vrais journaux de questions devront être anonymisés avant d'enrichir le jeu d'évaluation.

La phase suivante recommandée, toujours sans multi-document, est une campagne d'évaluation métier de 80 à 150 questions réelles avec validation des réponses, des citations et des refus par des experts BCM.
