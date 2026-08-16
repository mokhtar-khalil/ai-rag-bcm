# Phase 1 — Stabilisation technique du chatbot RAG BCM

## Objectif de cette phase

Cette phase rend le prototype fiable, configurable et testable avant d'améliorer le retrieval. Elle ne transforme pas encore le moteur TF-IDF en recherche sémantique avancée : cette amélioration appartient à la phase suivante et devra être mesurée sur un jeu de questions de référence.

Le résultat attendu est une base saine : un même code peut fonctionner en développement, dans les tests et derrière un serveur de production, sans exposer la clé API ni dépendre d'un appel externe pendant les tests.

## 1. Configuration centralisée

Le module `core/config.py` est désormais l'unique point de lecture de `.env`. Il convertit et valide chaque valeur avant le démarrage.

| Variable | Rôle | Valeur par défaut |
|---|---|---|
| `APP_ENV` | profil `development`, `test` ou `production` | `development` |
| `REPORT_PATH` | chemin du PDF source | `data/Rapport annuel 2025-BCM.pdf` |
| `INDEX_PATH` | chemin de l'index persistant | `storage/bcm_index.joblib` |
| `LOG_LEVEL` / `LOG_DIR` | niveau et dossier des journaux | `INFO` / `logs` |
| `API_HOST` / `API_PORT` | écoute de Flask | `127.0.0.1` / `5000` |
| `GRADIO_HOST` / `GRADIO_PORT` | écoute de Gradio | `127.0.0.1` / `7861` |
| `TOP_K` | nombre de premiers résultats utilisés | `5` |
| `RETRIEVAL_CANDIDATES` | nombre de candidats avant sélection | `12` |
| `MIN_RELEVANCE_SCORE` | seuil minimal de pertinence | `0.075` |
| `MAX_QUESTION_CHARS` | longueur maximale d'une question | `2000` |
| `MAX_HISTORY_MESSAGES` | taille maximale de l'historique transmis | `8` |
| `MAX_REQUEST_BYTES` | taille maximale du corps HTTP | `65536` |
| `REINDEX_TOKEN` | jeton de réindexation en production | vide, donc route désactivée |

Une valeur hors limite provoque une erreur explicite au démarrage. Par exemple, `TOP_K=0`, un port supérieur à 65535 ou `APP_ENV=staging` sont refusés. Cela évite les erreurs silencieuses qui n'apparaîtraient qu'au moment d'une question utilisateur.

Les chemins relatifs sont toujours résolus depuis la racine du projet. L'application ne dépend donc plus du dossier depuis lequel la commande a été lancée.

## 2. API Flask isolée et testable

`create_app()` construit maintenant une instance autonome de l'API. Chaque instance reçoit :

- sa configuration ;
- son moteur `RAGIndex` ;
- son verrou d'indexation ;
- ses routes et ses gestionnaires d'erreurs.

Cette fabrique d'application évite qu'un test modifie l'état d'un autre test. Elle permet aussi d'injecter une configuration ou un moteur factice sans toucher à la configuration réelle.

Les imports sont absolus (`api.rag`, `api.providers`, `core.config`). Le lancement `python -m api.app`, l'import WSGI et les tests utilisent ainsi exactement les mêmes modules.

## 3. Validation d'une requête

Pour `POST /api/ask`, le backend applique l'ordre suivant :

1. contrôle de la taille HTTP globale ;
2. vérification que le corps est un JSON valide ;
3. vérification que ce JSON est un objet ;
4. présence d'une question textuelle non vide ;
5. contrôle de la longueur de la question ;
6. nettoyage de l'historique.

Seuls les rôles `user` et `assistant` sont conservés. Chaque contenu d'historique est borné à 4 000 caractères et seuls les derniers messages configurés sont utilisés.

## 4. Erreurs uniformes et traçables

Les erreurs `400`, `404`, `405`, `413` et `500` renvoient toujours du JSON :

```json
{
  "error": "Description utilisable par l'interface.",
  "request_id": "identifiant-technique"
}
```

Chaque réponse contient également l'en-tête `X-Request-ID`. Si un reverse proxy fournit un identifiant sûr, il est conservé ; sinon l'API en crée un. Cet identifiant permet de retrouver l'événement correspondant dans les journaux sans enregistrer la question de l'utilisateur.

Une erreur interne renvoie un message générique. La pile technique reste uniquement dans le journal serveur afin de ne pas révéler la structure interne de l'application.

## 5. Journaux sûrs et bornés

En développement, `core/logging_config.py` crée `logs/api.log` avec rotation :

- taille maximale de 5 Mio par fichier ;
- trois anciennes versions conservées ;
- niveau réglé par `LOG_LEVEL` ;
- date, niveau, méthode, route, statut, durée et identifiant de requête.

Les journaux ne contiennent volontairement ni question, ni réponse, ni historique, ni clé API. Pour une erreur de fournisseur, seuls son type interne et la classe de l'exception sont consignés.

En production, les événements partent vers la sortie standard et sont confiés au gestionnaire de services. Cette séparation évite que plusieurs workers Gunicorn essaient de faire tourner le même fichier simultanément.

## 6. Trois profils d'exécution

### Développement local

```bash
./run.sh
```

Le script valide la configuration, vérifie que les ports sont libres, lance Flask puis Gradio, attend réellement que les deux services répondent et ouvre éventuellement le navigateur. `Ctrl+C` arrête les deux processus et évite de laisser un serveur orphelin.

### Tests

```bash
APP_ENV=test GENERATION_PROVIDER=extractive .venv/bin/python -m pytest -q
```

`tests/conftest.py` impose également ces valeurs. Aucune requête vers un moteur externe n'est donc effectuée et aucune clé locale n'est consommée.

### API de production

```bash
# Dans .env : APP_ENV=production
./run_api_prod.sh
```

Ce script refuse de démarrer si le profil n'est pas `production`, puis sert `wsgi:app` avec Gunicorn. En production, `POST /api/reindex` est désactivé lorsque `REINDEX_TOKEN` est vide. Avec un jeton configuré, l'appel doit contenir `Authorization: Bearer <jeton>`. L'intégration au site BCM, le proxy HTTPS et la supervision relèvent de la phase de déploiement ; ce script fournit le point d'entrée WSGI correct.

## 7. Installation reproductible

`setup.sh` suit maintenant cet ordre :

1. création de `.env` s'il manque ;
2. création de `.venv` ;
3. installation de `requirements-dev.txt` ;
4. validation de la configuration et de la présence du PDF ;
5. reconstruction de l'index ;
6. exécution des tests.

`requirements.txt` contient les dépendances d'exécution. `requirements-dev.txt` ajoute uniquement les outils de test.

## 8. Couverture automatisée

La suite comprend 17 tests. Elle vérifie notamment :

- les 127 pages du rapport et le chargement de l'index ;
- la récupération de faits connus ;
- le refus d'une question hors document ;
- les réponses factuelles attendues ;
- les questions vides ou trop longues ;
- le JSON invalide ;
- les erreurs 404 en JSON ;
- la propagation de `X-Request-ID` ;
- l'absence du nom du fournisseur dans les métadonnées publiques ;
- le blocage de la réindexation non authentifiée en production ;
- les chemins de configuration et le rejet des valeurs invalides.

La commande de validation exécutée à la fin de cette phase est :

```bash
.venv/bin/python -m compileall -q api core frontend scripts tests wsgi.py
.venv/bin/python scripts/check_config.py
APP_ENV=test GENERATION_PROVIDER=extractive .venv/bin/python -m pytest -q
```

Résultat : **17 tests réussis**. Les avertissements restants proviennent du chargement Joblib avec NumPy et ne correspondent pas à un échec fonctionnel.

## 9. Ce que cette phase change pour l'utilisateur

- les démarrages et arrêts sont plus fiables ;
- les erreurs de l'interface peuvent être reliées à un événement serveur ;
- une mauvaise configuration est détectée avant la mise en service ;
- les tests ne consomment plus le service de génération ;
- la base est prête pour mesurer objectivement les améliorations du retrieval.

La prochaine phase devra commencer par un jeu d'évaluation composé de questions, reformulations, réponses attendues et pages sources. Sans ce jeu, toute modification du chunking, des embeddings ou du reranking resterait subjective.
