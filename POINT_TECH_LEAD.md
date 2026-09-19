# Assistant IA BCM — Point technique (préparation réunion tech lead)

Chatbot de questions-réponses **fondé uniquement sur les documents publiés par la BCM** :
Rapport annuel 2025 (PDF) + Lettres d'information mensuelles 2026 (images OCRisées). Toute question hors de ce corpus reçoit un refus explicite — pas de génération libre.

---

## 1. Vue d'ensemble en une phrase

Un pipeline RAG (Retrieval-Augmented Generation) hybride lexical + sémantique, avec reformulation et reranking par LLM, garde-fous de pertinence, extraction visuelle locale des graphiques, mémoire conversationnelle, et support bilingue FR/AR — exposé via une API Flask, consommé par un widget web embarquable.

```
Site bcm.mr ──<script>── Railway (widget + API Flask + index RAG, un seul service)
```

---

## 2. Architecture des composants

| Dossier | Rôle |
|---|---|
| `api/` | API Flask : orchestration RAG (`app.py`), index hybride (`rag.py`), fournisseurs LLM (`providers.py`), analyse graphique locale (`charts.py`), embeddings (`embeddings.py`), gestion multi-sources (`sources.py`), expansion de requête (`query.py`) |
| `core/` | Configuration centralisée et validée (`config.py`), langue/RTL arabe (`language.py`), journalisation (`logging_config.py`) |
| `frontend/` | Interface Gradio — démo interne uniquement, jamais exposée publiquement |
| `widget/` | Widget JS pur embarquable, sans dépendance, servi statiquement |
| `data/` | Documents sources (PDF rapport + lettres) |
| `storage/` | Index persistant (`.joblib`), cache modèle d'embedding, cache pages/OCR de graphiques |
| `scripts/` | Construction manuelle de l'index, OCR, évaluation |
| `evaluation/` | Jeux de questions de référence + résultats de benchmark |
| `tests/` | 58 tests automatisés, aucun appel externe |

Le backend Flask (`api/`, `core/`) est **le seul composant à déployer en production**. Gradio est un outil de démo interne.

---

## 3. Pipeline de traitement d'une question (`/api/ask`)

Le cœur du système est dans `api/app.py`, fonction `_ask_events()` — un générateur d'événements partagé entre `/api/ask` (réponse unique) et `/api/ask/stream` (Server-Sent Events).

**Étapes séquentielles :**

1. **Validation** — JSON, taille, longueur de question, historique (max 16 messages / 8 tours), langue (`fr`/`ar` explicite, ou détection de secours).
2. **Contextualisation de suivi** — si la question référence un échange antérieur (« cette section », « ce graphique », « répète... »), elle est reformulée en question autonome à partir de l'historique. Une demande de répétition exacte court-circuite tout le pipeline et renvoie la réponse mémorisée sans nouvelle génération.
3. **Recherche hybride (retrieve)** — combinaison de 3 signaux dans `RAGIndex.retrieve()` (`api/rag.py`) :
   - TF-IDF mots + bigrammes (78 % du score lexical)
   - TF-IDF caractères 3-5-grammes (22 % du score lexical) — robuste aux fautes/variations
   - similarité cosinus sur embeddings sémantiques locaux (384 dim, `intfloat/multilingual-e5-small`, via Sentence Transformers, 100 % local — aucun envoi du corpus à un service externe)
   
   Fusion par **Reciprocal Rank Fusion** (RRF) plutôt qu'addition directe des scores (échelles non comparables) :
   `score = (1-w)/(60+rang_lexical) + w/(60+rang_sémantique)`, `w = 0.45` par défaut.
4. **Reformulation multiple (query planning)** — si l'appariement initial est fragile (score bas, faible couverture de mots-clés, question courte peu spécifique), un appel OpenAI produit jusqu'à 4 reformulations autonomes. Chacune relance une recherche locale ; les résultats sont fusionnés par RRF. Déclenché seulement si nécessaire — pas systématique — pour limiter coût et latence.
5. **Références explicites / mémoire** — un numéro de tableau ou de graphique cité (« tableau 5 ») est résolu vers sa vraie page PDF (distincte de la pagination imprimée) et épinglé en tête des résultats ; idem pour les pages déjà citées dans la conversation.
6. **Reranking (sélection)** — un second appel OpenAI (modèle dédié, JSON strict imposé) affine le classement final des passages candidats avant rédaction, uniquement si le provider est OpenAI et que le garde-fou de pertinence est déjà franchi.
7. **Garde-fou de pertinence (`is_relevant`)** — double preuve requise (lexicale OU sémantique avec recouvrement de mots-clés) avant d'autoriser la génération. Seuil sémantique calibré à `0.88` car les similarités du modèle local restent élevées même pour des questions hors sujet — un seuil naïf (`0.32`) acceptait à tort toutes les questions hors corpus.
8. **Voie graphique locale** (si la question porte sur un graphique) — voir section 5.
9. **Expansion contextuelle** — ajout des chunks voisins (page suivante/précédente d'un même tableau) tronqués par le découpage.
10. **Génération** — réponse rédigée strictement à partir des passages sélectionnés, avec citations `[p. PDF N]` obligatoires. Repli extractif (affichage brut des meilleurs passages) si le LLM échoue.
11. **Contrôle post-génération** — une réponse qui ne cite aucune source est traitée comme un refus : les sources ne sont pas publiées (éviter l'impression trompeuse que des passages « appuient » une réponse en fait non fondée).

### Chunking (indexation)

- Découpage par paragraphes avec chevauchement (`chunk_size=1150`, `overlap=180`) pour préserver le contexte inter-phrases.
- Découpage **ligne à ligne dédié aux tableaux chiffrés** (`_table_line_chunks`) : détecte les lignes à forte densité numérique, associe l'en-tête de colonnes/années et le contexte de libellé le plus proche — évite de diluer une valeur isolée dans un paragraphe générique.
- Version de schéma d'index (`INDEX_SCHEMA_VERSION`) et empreinte SHA-256 du corpus : reconstruction automatique si le contenu change.

---

## 4. Génération (LLM)

Contrôlé par `GENERATION_PROVIDER` (`.env`) :

| Mode | Comportement |
|---|---|
| `auto` (par défaut) | OpenAI si clé dispo → sinon Ollama local si dispo → sinon `extractive` |
| `openai` | modèle configurable (`OPENAI_MODEL`, `OPENAI_RERANK_MODEL`), `OPENAI_REASONING_EFFORT` |
| `gemini` | alternative supportée, avec `GEMINI_THINKING_LEVEL` |
| `ollama` | modèle local (ex. `llama3.2:3b`), aucune donnée envoyée à l'extérieur |
| `extractive` | pas de LLM : restitution directe des meilleurs passages — utilisé en tests |

Points d'attention techniques :
- Les tokens de raisonnement (reasoning/thinking) des modèles récents sont **décomptés du plafond de sortie** — un plafond trop serré ne réduit pas le coût (seuls les tokens produits sont facturés) mais tronque ou vide la réponse. D'où des plafonds larges calibrés empiriquement (`OPENAI_MAX_OUTPUT_TOKENS=3000`, `GEMINI_MAX_OUTPUT_TOKENS=8000`).
- Les 2 appels JSON (planification, reranking) imposent un schéma de sortie structuré — sans cela, JSON invalide dans ~50 % des cas (accolade manquante), coûtant l'appel entier en pure perte.
- La clé API reste strictement côté backend Flask, jamais exposée au navigateur.

---

## 5. Analyse locale des graphiques (`api/charts.py`)

Chaîne entièrement **exécutée sur le serveur, sans envoi d'image à un service externe** :

1. Détection d'intention (mots-clés + formulations mesure/périodicité type « volume des virements par mois »).
2. Recherche thématique élargie (jusqu'à 30 candidats).
3. Sélection de page (numéro explicite prioritaire, sinon proximité thématique).
4. Rendu PDF → PNG local (`pdftoppm`, 170 dpi).
5. OCR local via un petit exécutable **Swift/Apple Vision** (`scripts/chart_ocr.swift`) — donc dépendant de macOS pour cette étape uniquement.
6. Cadrage (le titre le plus proche isole la bonne figure sur une page qui en contient plusieurs).
7. Lecture structurée (années/séries/valeurs reliées par position, échelle reconstruite à partir de plusieurs graduations pour amortir une erreur OCR isolée).
8. Explication rédigée combinant lecture visuelle + texte natif de la page, citation systématique `[p. PDF N]`.
9. Cache local (`storage/chart_pages/`) — premier traitement de quelques secondes, requêtes suivantes instantanées.

Couvre aussi : organigramme (gouvernance/directions), et les 4 états financiers (situation financière, résultat net, variations des capitaux propres, flux de trésorerie) avec comparaison automatique de deux clôtures.

**Limite connue :** dépendance macOS/Apple Vision pour l'OCR — à surveiller si le déploiement production doit tourner sur Linux (le rapport indique que l'OCR est pré-calculé et versionné pour les Lettres d'information, donc le serveur Linux de prod n'a besoin ni du moteur OCR ni des images pour ce corpus — mais la voie graphique interactive du rapport annuel reste macOS-dépendante en l'état).

---

## 6. Multi-sources et citations

Le corpus combine deux types de documents (`api/sources.py`) :

| Source | Format | Repère cité |
|---|---|---|
| Rapport annuel | PDF natif | `[p. PDF N]` |
| Lettres d'information | Image → PDF paginé → OCR Apple Vision (offline, macOS) | `[Lettre d'information Mois AAAA, p. N]` |

Le TF-IDF étant global au corpus, l'ajout de nouvelles sources redistribue légèrement les scores existants — deux réglages (`RETRIEVAL_CANDIDATES`, profondeur de preuves pour question large) ont été élargis pour absorber ce reclassement sans perte de rappel. Un mécanisme dédié (`_select_results`) empêche qu'une source n'évince silencieusement l'autre à budget de passages constant.

---

## 7. Support bilingue FR/AR

- Choix explicite de langue transmis à chaque requête (prioritaire sur la langue détectée dans le texte).
- Question arabe : enrichissement local via glossaire bilingue + interrogation de l'index sémantique multilingue — **une seule requête distante** si le premier passage est probant (pas de traduction ni reranking séparés).
- Réponse imposée en arabe standard moderne, rendu bidirectionnel Unicode (isolats directionnels autour des nombres/dates/sigles pour empêcher le navigateur d'inverser leur ordre).
- Extraits sources bruts (rédigés en français dans le PDF) masqués en interface arabe pour éviter un affichage bilingue incohérent ; pages et scores restent visibles.
- Tolérance explicite de sigles techniques légitimes (`ISO 20022`, `RTGS`, `SWIFT`, `GIMTEL`) dans une réponse arabe sinon jugée « non traduite ».

---

## 8. Mémoire conversationnelle

- Stateless côté serveur : le client (widget/Gradio) renvoie l'historique complet (max 8 tours / 16 messages) à chaque appel — pas de session persistée serveur.
- Résolution de références (« cette section », « ce graphique », sujet nommé plus ancien).
- Répétition exacte détectée et servie sans régénération (économie de coût/latence).
- OCR documentaire à la demande si une page déjà citée est scannée et peu extractible nativement (< 1200 caractères de texte natif).
- Le bouton « Nouvelle conversation » efface la mémoire côté client — rien n'est stocké côté serveur entre requêtes.

---

## 9. Performance et latence

Profil mesuré (question large) : génération 5,9 s, planification 2,9 s, reranking 2,8 s, recherche locale < 0,1 s. **Le temps est dominé par les appels LLM distants séquentiels**, pas par la recherche.

Optimisations notables :
- Streaming SSE (`/api/ask/stream`) — le temps total est inchangé mais l'utilisateur voit les premiers mots dès ~7 s au lieu d'un écran figé jusqu'à ~12 s. **Le texte diffusé est provisoire** : seul l'événement `done` a passé les contrôles de citation et le rendu bidirectionnel arabe.
- Préchargement du modèle d'embedding au démarrage de l'app (économise ~6 s sur la première question de chaque worker Gunicorn).
- Reformulation/reranking déclenchés **conditionnellement**, pas systématiquement.
- Nécessité de l'en-tête `X-Accel-Buffering: no` derrière un proxy nginx — sinon le tamponnage annule tout le bénéfice du streaming.

---

## 10. Configuration et exploitation

- Configuration centralisée et validée au démarrage (`core/config.py`) — toute valeur hors bornes lève une erreur explicite avant que le service ne serve une seule requête.
- 3 profils (`APP_ENV`) : `development`, `test` (aucun appel externe, génération extractive forcée), `production` (WSGI/Gunicorn, réindexation protégée par jeton `Bearer`).
- Journalisation : jamais de question, réponse, historique ni clé API dans les logs — uniquement type d'erreur, durée, statut, `request_id`.
- Rate limiting (`flask-limiter`), CORS strict par origine autorisée, taille de requête bornée.
- Index reconstruit **au build de l'image Docker** — pas de volume persistant, mise à jour du corpus = redéploiement.

---

## 11. Déploiement

```
bcm.mr (balise <script>) → Railway (widget JS + API Flask + index, un seul service)
```

- Railway héberge l'API, la page interne et le widget JS.
- L'équipe BCM n'a qu'une balise `<script>` à intégrer sur le site (`docs/INTEGRATION_EQUIPE_BCM.md`).
- CI/CD GitHub Actions : tests + lint + audit sécurité + build Docker sur chaque push (`ci.yml`), publication d'image sur merge `main` (`cd.yml`), promotion manuelle vers `:production` (`promote.yml`).
- Scénarios locaux/Docker/VM de test documentés dans `DEPLOYMENT.md`.

---

## 12. Qualité mesurée

Sur 41 cas d'évaluation (`evaluation/questions.jsonl`) :

| Mesure | Résultat |
|---|---:|
| Hit@5 | 97,14 % |
| Rappel@12 (candidats transmis au reranker) | 100 % |
| Refus hors corpus | 100 % |
| MRR | 0,7807 |

Comparaison à la baseline lexicale pure (avant embeddings) : Hit@5 passé de 70,59 % à 97,14 %. 58 tests automatisés (unitaires + intégration), aucun appel externe, aucune consommation de clé API en CI.

**Ces chiffres sont des mesures techniques, pas encore une validation métier BCM** — un expert métier doit encore confirmer les pages attendues et enrichir le jeu de questions avec des formulations réellement observées auprès des utilisateurs.

---

## 13. Limites connues à mentionner au tech lead

- **OCR graphique dépendant de macOS** (Apple Vision) — la voie interactive d'analyse de graphiques du rapport annuel ne peut pas tourner nativement sur le serveur Linux de production ; seul l'OCR pré-calculé des Lettres d'information est portable.
- **Mémoire conversationnelle stateless** : pas de persistance serveur, tout repose sur ce que le client renvoie à chaque appel — un client mal intégré peut casser la continuité de la conversation.
- **Ambiguïté vs faux positif de clarification** : un cas identifié où le système demande une clarification alors qu'il avait déjà trouvé la bonne réponse ; aucun seuil ne distingue encore ce cas d'une ambiguïté réelle (scores quasi identiques mesurés : 0,455 vs 0,486).
- **Scalabilité, observabilité, gouvernance des données** : phases 4-6 du roadmap, pas encore commencées (mentionné explicitement dans `DEPLOYMENT.md`).
- **Accès au vrai serveur BCM** : pas encore obtenu au moment de la dernière mise à jour de la documentation ; une VM VirtualBox sert de répétition générale.
- Le corpus reste volontairement mono-domaine (rapport + lettres) : pas de système multi-documents généralisé.

---

## 14. Questions probables du tech lead — pistes de réponse

- **« Pourquoi pas un vector store dédié (Pinecone, Weaviate...) ? »** → Corpus petit (quelques milliers de chunks), TF-IDF hybride + embeddings locaux stockés dans un `.joblib` suffisent, évitent une dépendance infra supplémentaire et gardent tout local/offline.
- **« Pourquoi RRF plutôt que fusion pondérée directe des scores ? »** → Échelles TF-IDF et cosinus non comparables ; les rangs le sont.
- **« Comment est garanti que le modèle ne répond pas hors corpus ? »** → Double garde-fou : seuil de pertinence avant génération + contrôle post-génération (réponse sans citation `[...]` = traitée comme refus, sources non publiées).
- **« Coût par question ? »** → Dominé par 1 à 3 appels LLM (recherche/planification optionnelle, reranking optionnel, génération) ; les tokens de raisonnement comptent dans le plafond de sortie mais ne sont facturés que s'ils sont réellement produits.
- **« Pourquoi avoir abandonné Vercel ? »** → Une seule origine (Railway) sert la page, le widget et l'API : plus de CORS à négocier entre deux plateformes, une plateforme de moins à exploiter et à facturer pendant le pilote. Voir section 16.
- **« Comment la vie privée est-elle protégée dans l'analytics ? »** → Rien n'est conservé sans consentement explicite, les identifiants de session sont pseudonymisés par HMAC, aucune IP/nom/email n'est collecté, et l'export par défaut est agrégé. Voir section 15.
- **« Pourquoi un seul worker Gunicorn en production ? »** → Le modèle d'embedding pèse 811 Mo par processus ; chaque worker en charge sa propre copie. Le temps de réponse est de toute façon dominé par l'attente des appels LLM distants, pas par le calcul local — un worker + plusieurs threads couvre la concurrence sans dupliquer le modèle. Voir section 16.

---

## 15. Analytics du pilote — collecte, confidentialité, dashboard

### Objectif

Le pilote doit permettre de répondre, à la fin de la période d'essai, à cinq
questions : combien de sessions et de questions, quels sujets/publications
intéressent le plus, quelles réponses sont utiles ou refusées, quelles sont la
latence/les erreurs/la consommation de tokens, et quoi prioriser avant une
généralisation. Le module analytics (`api/analytics.py`, `api/usage.py`,
`docs/ANALYTICS_PILOTE.md`) a été construit spécifiquement pour ça — ce n'est
pas de la télémétrie générique, mais un outil de décision pour BCM.

### Ce qui a changé — d'un ajout minimal à un système complet

Deux commits successifs : `83b01d5` posait la base (table unique, popup de
consentement, PostgreSQL en option) ; `8a4f3ed` (« Renforcement des
analytics ») l'a étendue à un schéma à 4 tables, un collecteur de tokens dédié,
le retour utilisateur (pouce haut/bas + motif), un endpoint agrégé protégé et
un générateur de bilan — environ 2 500 lignes ajoutées au total sur les deux
commits, dont un guide complet (`docs/ANALYTICS_PILOTE.md`, 263 lignes).

### Schéma de données (4 tables)

| Table | Contenu | Condition |
|---|---|---|
| `logged_questions` | Une ligne par réponse : langue, question, réponse, thème, statut du pipeline, sources, score, latence, tokens agrégés, fournisseur/modèle | Consentement requis |
| `model_calls` | Une ligne par appel distant (`query_planning`, `reranking`, `generation`) : fournisseur, modèle, latence, succès, tokens entrée/entrée-cache/sortie/raisonnement/total | Toujours (pas de texte) |
| `answer_feedback` | Pouce haut/bas, `resolved`, motif (`incorrect`, `incomplete`, `missing_source`, `unclear`, `too_slow`, `other`), lié au `response_id` par jeton HMAC signé | Un seul vote par session et par réponse |
| `ui_events` | Parcours : ouverture, question posée, suggestion cliquée, réponse reçue, sources ouvertes, feedback, limite de session atteinte | Consentement requis, jamais de texte de question |

### Nouvelles routes API

- `POST /api/feedback` — enregistre un vote, protégé par le jeton signé du `response_id` (empêche un vote forgé ou répété).
- `POST /api/events` — enregistre un événement de parcours consenti.
- `GET /api/admin/analytics?days=N` — agrégats JSON uniquement (aucune question/réponse brute), protégé par `Authorization: Bearer <ANALYTICS_ADMIN_TOKEN>`. C'est la voie recommandée pour brancher un dashboard central sans lui donner accès à la base brute.

### Collecteur de tokens (`api/usage.py`)

Isolation par requête via `contextvars` (deux requêtes concurrentes ne
mélangent jamais leurs compteurs). Normalise trois formats de SDK différents
(OpenAI `usage.input_tokens_details.cached_tokens`, Gemini
`usage_metadata.thoughts_token_count`, Ollama `prompt_eval_count`/`eval_count`)
vers un schéma commun : entrée, entrée cachée, sortie, raisonnement, total. Un
compteur à zéro signifie que le fournisseur ne l'a pas renvoyé — à ne pas lire
comme une consommation réellement nulle.

### Confidentialité — règles appliquées

- Le texte (question/réponse) n'est conservé qu'après acceptation explicite du popup ; un refus n'empêche pas d'utiliser l'assistant.
- Cliquer sur le feedback vaut accord ponctuel pour cette seule réponse.
- `session_id` pseudonymisé par HMAC (`ANALYTICS_HASH_SALT`, à ne jamais committer, à ne jamais changer pendant le pilote sous peine de fragmenter les statistiques).
- Aucune IP, aucun nom, aucun email collecté.
- L'export standard (`scripts/analytics_report.py`) est agrégé ; `--include-content` (questions/réponses brutes) est réservé aux analystes habilités.
- Durée de conservation et personnes habilitées : à fixer avec le responsable métier BCM, pas encore tranché dans le code.

### Dev vs production

SQLite (`storage/analytics.db`) en développement — pratique mais son fichier
disparaît à chaque redéploiement Railway sans volume persistant. En
production, `DATABASE_URL` (PostgreSQL, ajouté comme extension Railway) est
requis pour la durabilité ; `scripts/check_config.py` refuse même le démarrage
en production sans `ANALYTICS_HASH_SALT` défini. Une panne du module
analytics est journalisée mais **ne bloque jamais** une réponse à
l'utilisateur — c'est un système d'observation, pas un chemin critique.

### KPI définis (`docs/ANALYTICS_PILOTE.md`)

```text
Satisfaction = votes positifs / votes exprimés
Couverture du feedback = réponses évaluées / interactions consenties
Résolution = retours resolved=true / retours ayant répondu à la question
Réponses sourcées = grounded=true / interactions
Refus = status=refused / interactions
Questions par session = interactions / sessions consenties
Tokens par interaction = total_tokens / interactions
```

Règle imposée dans le guide : toujours afficher le numérateur et le
dénominateur — un taux de 100 % sur 2 votes n'est pas une conclusion.

### Premier résultat réel (`storage/bilan_pilote.md`, généré le 3 sept. 2026)

| Indicateur | Valeur |
|---|---:|
| Sessions consenties / Interactions | 6 / 7 |
| Satisfaction | 71,4 % |
| Besoins résolus | 66,7 % |
| Réponses sourcées | 100 % |
| Latence p50 / p95 | 10,0 s / 21,2 s |
| Tokens totaux (7 interactions) | 47 239 |

Le rapport se signale lui-même comme **non concluant** : échantillon trop
petit (le guide recommande 30 interactions minimum avant toute lecture
métier). Utile surtout comme preuve que toute la chaîne fonctionne de bout en
bout (consentement → journalisation → feedback → bilan) avant la montée en
charge du pilote.

---

## 16. Centralisation du widget sur Railway (suppression de Vercel)

### Avant / après

**Avant** (`ce989b7`^) : l'API tournait sur Railway et le widget statique sur
Vercel — deux plateformes, deux domaines, un en-tête CORS à négocier entre les
deux, une URL d'API injectée au build par `scripts/build_widget_vercel.sh`.

**Après** : un seul service Railway sert les trois choses :

```
Railway (un seul service)
  ├─ GET  /                     page interne d'évaluation, HTTP Basic optionnel
  ├─ GET  /bcm-chat-widget.js   le widget, servi depuis la même origine
  └─ POST /api/*                l'API Flask + l'index RAG
```

`GET /` servait auparavant la description JSON du service ; elle est
déplacée sur `GET /api`. `vercel.json` et
`scripts/build_widget_vercel.sh` sont supprimés ; `DEPLOIEMENT_RAILWAY_VERCEL.md`
est renommé `docs/DEPLOIEMENT_RAILWAY.md`.

### Pourquoi

Même origine pour la page, le script et l'API → **aucun en-tête CORS
nécessaire** pendant la phase d'évaluation interne. `CORS_ALLOWED_ORIGINS`
redevient utile seulement le jour où le widget s'intègre sur un domaine bcm.mr
distinct — et reste vide en attendant. Une plateforme de moins à exploiter,
facturer et garder synchronisée avec l'API pendant le pilote.

### Contrôle d'accès de la page interne

`INTERNAL_ACCESS_USERNAME` / `INTERNAL_ACCESS_PASSWORD` protègent `/` par
authentification HTTP Basic (gérée nativement par le navigateur, aucune page
de connexion à développer) — comparaison en temps constant (`hmac.compare_digest`).
Les deux vides par défaut : la page reste ouverte à qui a le lien, un choix
assumé pour une démonstration courte. `/bcm-chat-widget.js`, lui, **n'est
jamais protégé** : un script JS n'est pas un secret, comme le code de
n'importe quelle page web une fois chargée.

### URL de l'API injectée dynamiquement

La page interne (`widget/internal.html`) reçoit l'URL de l'API par
substitution de `__API_BASE_URL__` / `__WIDGET_SCRIPT_URL__` à partir de
`request.url_root` **au moment de la requête** — jamais figée dans le dépôt ou
au build. Conséquence pratique : la page fonctionne identiquement sur le
sous-domaine Railway par défaut ou sur un domaine personnalisé, sans variable
à changer. Testé explicitement (`tests/test_internal_access.py`).

### Deux bugs découverts pendant la bascule

- **`Dockerfile` incomplet** (`a5a0fcc`) : `COPY widget ./widget` manquait — l'image se construisait sans erreur, mais `/` et `/bcm-chat-widget.js` renvoyaient 404 en production faute de fichiers présents dans le conteneur. Corrigé par un ajout d'une ligne, mais un bon exemple de pourquoi un `curl` de vérification post-déploiement (section « Vérifier après déploiement » de `DEPLOIEMENT_RAILWAY.md`) est nécessaire même pour un changement qui « build » sans erreur.
- **Schéma HTTP incorrect derrière le proxy Railway** (`58e4072`) : Railway termine le TLS à son edge et transmet en HTTP simple au conteneur. Sans `ProxyFix` (Werkzeug), `request.url_root` renvoyait `http://…` au lieu de `https://…`, ce qui aurait cassé l'URL de l'API injectée dans la page interne servie en HTTPS. Corrigé avec `ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)` — un seul saut de confiance (l'edge Railway).

### Découvertes de performance en production (pendant cette période)

Deux réglages mesurés et documentés dans `docs/DEPLOIEMENT_RAILWAY.md`,
directement pertinents pour discuter du dimensionnement avec le lead :

| Sujet | Mesure | Réglage retenu |
|---|---|---|
| Mémoire par worker Gunicorn | Le modèle d'embedding pèse **811 Mo** par processus ; chaque worker en charge sa propre copie (2 workers ≈ 1,6 Go, 4 workers > 3 Go) | `WEB_CONCURRENCY=1` + `GUNICORN_THREADS=4` — la latence est dominée par l'attente des appels LLM distants, pas par le calcul local, donc les threads suffisent à couvrir la concurrence sans dupliquer le modèle |
| Contention CPU de torch | Une question inédite prenait **6 à 9 s** en production contre **0,15 s** en local (le cache d'embedding renvoyait une question déjà posée en 0,01 s, isolant la cause au calcul du vecteur) — torch dimensionnait son parallélisme sur les cœurs annoncés par l'hôte, pas sur le quota réel du conteneur | `OMP_NUM_THREADS=1` dans le `Dockerfile` → 1,41 s → 0,15 s sur 1 vCPU. Ce coût se multiplie par le nombre de reformulations (jusqu'à 4) : non réglé, cette seule étape atteignait 29 s |

Effet de bord à surveiller : le compteur du rate limiter vit en mémoire du
processus. Correct avec un seul worker ; **passer à plusieurs workers ou
plusieurs répliques nécessitera d'abord de brancher Redis sur le limiteur**,
sans quoi la limite annoncée serait multipliée par le nombre de processus.

Autres repères retenus : image Docker ≈ 1,2 Go (torch/sentence-transformers
1,3 Go, `storage/` 487 Mo, `data/` 80 Mo) ; Railway est en `amd64` — ne jamais
publier une image construite sur un Mac Apple Silicon (arm64), la CI force
`platforms: linux/amd64`.

### Reste à faire pour l'intégration publique bcm.mr

1. Renseigner `CORS_ALLOWED_ORIGINS` avec les domaines exacts de la BCM (`https://bcm.mr` et `https://www.bcm.mr` sont deux origines distinctes pour le navigateur — les deux si le site répond sur les deux).
2. Retirer `INTERNAL_ACCESS_USERNAME`/`PASSWORD` une fois l'accès interne devenu inutile.
3. Suivre `docs/INTEGRATION_EQUIPE_BCM.md` pour la balise `<script>` à transmettre à l'équipe technique BCM.
