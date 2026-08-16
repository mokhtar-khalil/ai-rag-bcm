# Étapes de construction du chatbot RAG BCM

## 1. Définition du périmètre documentaire

Le chatbot doit répondre uniquement à partir du rapport annuel de la Banque Centrale de Mauritanie. Le PDF `Rapport annuel 2025-BCM.pdf` est donc la seule source autorisée. Cette contrainte empêche le système de compléter une réponse avec des informations externes.

## 2. Organisation du projet

Le projet a été séparé en couches :

- `frontend/` contient l'interface Gradio ;
- `api/` contient l'API Flask, le moteur RAG et la génération ;
- `data/` contient le rapport source ;
- `storage/` contient l'index persistant ;
- `scripts/` contient la construction de l'index ;
- `tests/` vérifie les réponses connues et les refus hors sujet.

## 3. Extraction du rapport PDF

`pypdf` lit les 127 pages et conserve le numéro de page PDF de chaque texte extrait. Le nettoyage retire les en-têtes répétitifs, les numéros de page isolés, les espaces superflus et les césures de fin de ligne.

## 4. Découpage en passages

Le texte nettoyé est découpé en passages d'environ 1 150 caractères, avec un chevauchement d'environ 180 caractères. Le chevauchement évite de perdre une information située à la frontière entre deux passages. Chaque passage garde son identifiant et sa page PDF.

## 5. Création de l'index RAG

Trois représentations complémentaires sont créées :

- mots et groupes de deux mots pour reconnaître les concepts précis ;
- groupes de 3 à 5 caractères pour mieux gérer les variantes, accents et formes proches.
- embeddings multilingues locaux de 384 dimensions pour reconnaître les reformulations.

Le score lexical combine 78 % de mots et 22 % de caractères. Le classement final fusionne ensuite 55 % du rang lexical et 45 % du rang sémantique. L'index, les matrices, les passages et les métadonnées sont enregistrés dans `storage/bcm_index.joblib`.

## 6. Détection des modifications

Une empreinte SHA-256 du PDF est enregistrée dans l'index. Au démarrage, l'application compare cette empreinte au fichier actuel. Si le rapport a changé, l'index est automatiquement reconstruit.

## 7. Construction de l'API Flask

L'API expose trois routes principales :

- `GET /health` vérifie que le service et l'index sont disponibles ;
- `POST /api/ask` reçoit une question et retourne une réponse avec ses sources ;
- `POST /api/reindex` reconstruit manuellement l'index.

La question est validée avant toute recherche : elle doit être non vide et ne pas dépasser la taille autorisée.

## 8. Recherche des passages candidats

La question est enrichie par un glossaire métier déterministe, puis transformée en vecteurs TF-IDF et sémantique local. Le moteur fusionne les rangs, récupère jusqu'à 12 candidats, pénalise les tables des matières et évite de sélectionner trop de passages venant de la même page.

Pour une question nationale sans pays explicitement mentionné, les pages du chapitre international sont écartées afin d'éviter les réponses portant sur une autre économie.

## 9. Reranking sémantique

Un second classement examine les candidats et conserve au maximum huit passages qui répondent réellement à la question. Un passage contenant seulement un mot commun avec la question est rejeté. Si aucun passage ne fournit de preuve suffisante, le chatbot répond que l'information n'est pas présente dans le rapport.

## 10. Génération de la réponse

Le moteur de langage reçoit seulement :

- la question ;
- un court historique utile ;
- les passages retenus avec leurs pages PDF ;
- des instructions strictes interdisant toute connaissance externe.

Pour une question factuelle, la consigne impose une réponse directe de quelques phrases. Les nombres, unités, périodes et comparaisons doivent être reproduits exactement.

## 11. Validation des citations

Après génération, le backend vérifie que chaque citation correspond à une page réellement fournie au moteur. Une page inventée déclenche le mode de secours. Les sources affichées dans Gradio contiennent le numéro de page, un extrait et le score de pertinence.

## 12. Mode de secours

Si le service de génération rencontre une erreur, l'application construit une réponse extractive avec les phrases les plus pertinentes des passages récupérés. Le chatbot reste ainsi disponible sans produire une réponse non sourcée.

## 13. Construction de l'interface Gradio

L'interface fournit :

- une zone de conversation ;
- un champ de question ;
- des exemples de questions ;
- un indicateur de disponibilité de l'API ;
- les citations et extraits utilisés ;
- un bouton pour réinitialiser la conversation.

Gradio communique avec Flask par JSON sur le réseau local. Les secrets restent dans le backend et ne sont pas transmis au navigateur.

## 14. Tests de qualité

Les tests automatisés vérifient notamment :

- le chargement des 127 pages ;
- la récupération du taux de croissance de 4,0 % ;
- la récupération des réserves de 2,2 milliards de dollars et 5,9 mois d'importations ;
- le rejet d'une question hors rapport ;
- la validation des requêtes vides ;
- la longueur raisonnable des réponses.
- la configuration et ses valeurs invalides ;
- le format uniforme des erreurs JSON ;
- l'identifiant de requête renvoyé par l'API.
- la fusion lexicale et sémantique ;
- l'expansion des formulations métier ;
- la distinction entre PIB réel total et activité non extractive.

## 15. Démarrage de l'application

`setup.sh` crée l'environnement Python, installe les dépendances, valide la configuration, construit l'index et exécute les tests. `run.sh` vérifie les ports, démarre Flask sur le port 5000 et Gradio sur le port 7861, attend que les deux services soient prêts, puis ouvre l'interface dans le navigateur. `Ctrl+C` arrête proprement les deux processus.

## 16. Résultat final

Le flux complet est le suivant : question utilisateur, validation, récupération des candidats, reranking sémantique, contrôle de pertinence, synthèse fondée sur le rapport, validation des citations, puis affichage dans Gradio.

Le diagramme complet est disponible dans `docs/diagrammes/architecture_complete.mmd` et ses versions image dans le même dossier.

Les détails de la stabilisation technique sont disponibles dans `PHASE_1_STABILISATION.md`.

Le benchmark et les détails du retrieval hybride sont disponibles dans `PHASE_2_QUALITE_RETRIEVAL.md`.
