# Déploiement — tout sur Railway

Un seul service héberge l'API, la page interne d'évaluation et le fichier du
widget :

```
Railway (un seul service)
  ├─ GET  /                     page interne, protégeable par mot de passe
  ├─ GET  /bcm-chat-widget.js   le widget, servi depuis la même origine
  └─ POST /api/*                l'API Flask + l'index RAG
```

Même origine pour la page, le script et l'API : aucun en-tête CORS n'est
nécessaire pour que l'assistant fonctionne à cette adresse. `CORS_ALLOWED_ORIGINS`
redevient utile le jour où le widget s'intègre sur un domaine bcm.mr distinct
(voir `docs/INTEGRATION_EQUIPE_BCM.md`) ; en attendant, il peut rester vide.

Une plateforme séparée (Vercel ou autre) pour le widget n'est plus utilisée :
le fichier ne change pas, seule son adresse de service change.

## 1. API sur Railway

### Ce qui est déjà prêt

`railway.json` décrit la construction et la sonde de santé. Railway détecte le
`Dockerfile` et construit l'image ; l'index est bâti **pendant la construction**
et vérifié :

```dockerfile
RUN python scripts/index_report.py \
 && python scripts/index_embeddings.py --if-configured \
 && python -c "… assert e.metadata['chunks'] > 2000, 'index incomplet'"
```

Conséquence utile : le conteneur démarre avec son corpus, sans volume persistant
ni accès réseau au premier appel. Mettre à jour le corpus = redéployer.

### Variables à définir dans Railway

| Variable | Valeur | Nature |
|---|---|---|
| `APP_ENV` | `production` | |
| `API_HOST` | `0.0.0.0` | |
| `GENERATION_PROVIDER` | `openai` | |
| `OPENAI_API_KEY` | … | **secret** |
| `OPENAI_MODEL` | `gpt-5.6-terra` | |
| `OPENAI_RERANK_MODEL` | `gpt-5.6-luna` | |
| `OPENAI_MAX_OUTPUT_TOKENS` | `3000` | |
| `CORS_ALLOWED_ORIGINS` | vide pour l'instant | requis lors de l'intégration publique |
| `INTERNAL_ACCESS_USERNAME` | `bcm` | protège `/` — voir `docs/ACCES_INTERNE_BCM.md` |
| `INTERNAL_ACCESS_PASSWORD` | secret long | idem, laisser vide = page ouverte à qui a le lien |
| `REINDEX_TOKEN` | … | **secret** |
| `RATE_LIMIT_ASK` | `20 per minute` | |
| `CHART_ANALYSIS_ENABLED` | `false` | |
| `WEB_CONCURRENCY` | `1` | **ne pas augmenter — voir ci-dessous** |
| `GUNICORN_THREADS` | `4` | |

Ne définissez **pas** `PORT` : Railway l'injecte, et la configuration lui donne
la priorité sur `API_PORT`.

### Pourquoi `WEB_CONCURRENCY=1`

Mesuré sur ce projet : un processus ayant chargé le modèle d'embedding occupe
**811 Mo**. Chaque worker `gunicorn` charge sa propre copie du modèle — deux
workers coûtent donc environ 1,6 Go, quatre plus de 3 Go. Sur les gabarits
courants de Railway, la montée en workers provoque un dépassement mémoire, pas
un gain de débit.

Un worker et quatre threads suffisent : le temps de réponse est dominé par
l'attente des appels distants, pas par le calcul local. Les threads couvrent
donc plusieurs requêtes simultanées sans dupliquer le modèle.

Ce choix a un second effet, souhaitable : le compteur de limitation de débit
vit en mémoire du processus. Avec un seul worker il est exact ; avec plusieurs,
chacun aurait son compteur et la limite annoncée serait multipliée d'autant.
**Si vous devez un jour passer à plusieurs workers ou plusieurs répliques, il
faudra d'abord brancher Redis sur le limiteur.**

### Temps de réponse : borner le parallélisme de torch

Symptôme observé en production : une question inédite mettait 6 à 9 secondes
avant même d'atteindre la génération, contre 0,15 s en local. Une question déjà
posée revenait en 0,01 s — le cache d'embedding — ce qui désignait clairement le
calcul du vecteur de la question.

Cause : torch dimensionne son parallélisme sur le nombre de cœurs annoncés par
l'hôte, pas sur le quota alloué au conteneur. Ses threads se disputent alors la
même fraction de CPU, et l'ordonnancement coûte plus qu'il ne rapporte.

Mesuré sur l'image, quatre questions inédites :

| | 1 vCPU | 2 vCPU |
|---|---|---|
| Sans réglage | 1,41 s | 0,36 s |
| `OMP_NUM_THREADS=1` | **0,15 s** | **0,11 s** |
| `OMP_NUM_THREADS=2` | — | 0,15 s |

Un seul thread gagne dans les deux cas : vectoriser une question courte ne tire
aucun profit du parallélisme. Le réglage est inscrit dans le `Dockerfile`, donc
actif sans configuration. **Ne l'augmentez pas** en pensant accélérer le service.

Ce coût se paie autant de fois qu'il y a de reformulations : lorsque le
planificateur en produit trois, la recherche enchaîne quatre vectorisations. Sur
la production non réglée, cette étape atteignait 29 secondes à elle seule.

### Analytics du pilote et limite de session

Trois fonctionnalités liées, toutes soumises au consentement demandé par le
widget avant la première question d'une session :

- **Consentement** : un popup s'affiche une fois par session ; en cas de
  refus, l'assistant reste utilisable, rien n'est journalisé.
- **Limite anti-abus** : `SESSION_MAX_QUESTIONS` (10 par défaut) questions par
  session, réinitialisée après `SESSION_IDLE_MINUTES` (30 par défaut)
  d'inactivité. Distincte de `RATE_LIMIT_ASK`, qui freine un débit trop rapide
  plutôt qu'un volume total.
- **Interactions** : réponse, statut RAG, sujet, sources, latence, fournisseur,
  modèle et tokens dans `logged_questions` et `model_calls`.
- **Parcours** : ouvertures, suggestions et consultation des sources dans
  `ui_events`, uniquement après consentement.
- **Retours** : satisfaction, résolution et motif dans `answer_feedback`. Le
  `response_id` et son jeton signé empêchent un vote forgé ou répété.
- **Vie privée** : aucune adresse IP ; la session aléatoire est pseudonymisée
  avec `ANALYTICS_HASH_SALT`.

#### Ajouter Postgres sur Railway

La journalisation exige une base **durable** : le disque d'un conteneur
Railway ne survit pas à un redéploiement sans volume attaché, et l'appli n'en
attache pas. Sans `DATABASE_URL`, un fichier SQLite local sert de repli —
correct en développement, mais silencieusement perdu au prochain déploiement
en production.

1. Dans le projet Railway : **New → Database → Add PostgreSQL**.
2. Railway injecte `DATABASE_URL` dans les variables du service API. Selon la
   façon dont les services sont liés, utilisez la référence de variable
   Railway affichée dans le projet plutôt que de recopier une URL en clair.
3. Ajoutez deux secrets longs et distincts : `ANALYTICS_HASH_SALT` et
   `ANALYTICS_ADMIN_TOKEN`.
4. Au démarrage, l'application crée ou migre `logged_questions`,
   `model_calls`, `answer_feedback` et `ui_events`. Une base indisponible ne
   bloque pas les réponses, mais produit un avertissement dans les logs.

Vérifier que la table existe :

```bash
railway connect postgres
```
```sql
\d logged_questions
\d model_calls
\d answer_feedback
\d ui_events
SELECT count(*) FROM logged_questions;
```

Le mode opératoire complet et le modèle de bilan figurent dans
`docs/ANALYTICS_PILOTE.md`.

### Diffusion des réponses

L'API renvoie la réponse en Server-Sent Events sur `/api/ask/stream`. La
réponse porte `X-Accel-Buffering: no`, indispensable derrière un proxy qui
tamponnerait sinon le flux et annulerait tout son bénéfice. Le widget bascule
seul sur l'appel unique `/api/ask` si le flux échoue.

### Deux façons de fournir l'image — aucune ne demande de la créer à la main

**Voie A — Railway construit depuis le dépôt** (déclarée dans `railway.json`,
recommandée pour la première mise en service).

Railway lit le `Dockerfile`, construit et déploie à chaque poussée sur la
branche suivie. Rien à publier, aucun identifiant de registre. La construction
prend plusieurs minutes : installation de torch, puis vectorisation des 2 595
passages.

**Voie B — Railway déploie l'image publiée par la CI.**

`.github/workflows/cd.yml` construit et publie déjà l'image sur GitHub
Container Registry à chaque fusion sur `main` :

```text
ghcr.io/<organisation>/<dépôt>-api:<sha>
ghcr.io/<organisation>/<dépôt>-api:staging
```

et `promote.yml` promeut manuellement un `sha` validé vers le tag `production`.
Dans Railway, on choisit alors **Deploy from Docker Image** et l'on vise
`…-api:production`. Le paquet GHCR doit être public, ou les identifiants de
registre renseignés dans Railway.

|  | Voie A | Voie B |
|---|---|---|
| Configuration | aucune | visibilité GHCR ou identifiants |
| Déploiement | reconstruit (plusieurs minutes) | téléchargement seul |
| Artefact déployé | reconstruit à part | **exactement celui que la CI a testé** |
| Déclenchement | poussée sur la branche | promotion explicite d'un `sha` |

Commencez par la voie A. La voie B apporte la traçabilité — on déploie
l'artefact exact qui a passé les tests — et redevient intéressante dès que les
redéploiements se multiplient ou qu'un contrôle de mise en production s'impose.

### Poids et architecture de l'image

Mesuré sur l'image construite :

| Composant | Taille |
|---|---|
| Dépendances Python (torch, sentence-transformers) | 1,3 Go |
| `storage/` (index et modèle d'embedding) | 487 Mo |
| `data/` (rapport PDF et Lettres) | 80 Mo |
| **Total transféré** | **≈ 1,2 Go** |

L'infrastructure de Railway est en `amd64`. Une image construite sur un Mac
Apple Silicon est en `linux/arm64` et n'y démarrerait pas : **ne publiez jamais
une image bâtie depuis un poste de développement**. La voie A construit sur
l'infrastructure cible, et la voie B force `platforms: linux/amd64` dans le
workflow — ce qui protège d'un basculement futur des runners GitHub vers ARM.

La première construction prend plusieurs minutes : l'installation de torch et la
vectorisation des 2 595 passages en représentent l'essentiel.

### Vérifier après déploiement

```bash
curl -s https://<projet>.up.railway.app/health | python3 -m json.tool
```

Attendu :

```json
{
  "status": "ok",
  "documents": 8,
  "pages_par_source": { "lettre": 43, "pdf": 127 },
  "chunks": 2595,
  "semantic_index": true,
  "chart_analysis_enabled": false
}
```

Un nombre de passages inférieur signale un index incomplet. Ce point d'entrée ne
publie volontairement ni empreinte de fichier ni chemin interne.

## 2. Page interne et widget

`/` sert une page d'évaluation avec le widget déjà configuré ; `/bcm-chat-widget.js`
sert le script depuis le même service. L'URL de l'API est déduite de la requête
elle-même (`request.url_root`), jamais figée dans le dépôt : la page fonctionne
identiquement sur le domaine Railway par défaut ou sur un domaine personnalisé,
sans variable à définir pour ça.

### Protéger l'accès

Tant que l'assistant n'est pas intégré publiquement sur bcm.mr, `/` peut être
protégée par un simple mot de passe HTTP (authentification Basic, gérée par le
navigateur — aucune page de connexion à construire) :

```dotenv
INTERNAL_ACCESS_USERNAME=bcm
INTERNAL_ACCESS_PASSWORD=<secret long>
```

Générer un secret :

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(18))"
```

Les deux variables vides (défaut) laissent la page accessible à qui a le lien —
suffisant pour une démonstration courte et fermée. `/bcm-chat-widget.js` n'est
jamais protégé : le script n'est pas un secret, comme le code de n'importe quelle
page web une fois chargée.

### Vérifier après déploiement

```bash
curl -sI https://<projet>.up.railway.app/bcm-chat-widget.js | grep -i "content-type\|cache-control"
curl -s -o /dev/null -w "%{http_code}\n" https://<projet>.up.railway.app/
```

Le second appel renvoie `401` si des identifiants sont configurés, `200` sinon.
Avec identifiants, ouvrir l'URL dans un navigateur déclenche l'invite native de
mot de passe — rien à développer côté page.

## 3. Ordre des opérations

1. Déployer sur Railway ; noter l'URL.
2. Générer et définir `INTERNAL_ACCESS_USERNAME` / `INTERNAL_ACCESS_PASSWORD`
   si l'accès doit rester fermé au-delà d'une démonstration ponctuelle.
3. Transmettre l'URL (et les identifiants, séparément) à l'équipe BCM.
4. Le jour de l'intégration publique sur bcm.mr : suivre
   `docs/INTEGRATION_EQUIPE_BCM.md`, renseigner `CORS_ALLOWED_ORIGINS` avec
   leurs domaines exacts, et retirer les identifiants d'accès interne.

`https://bcm.mr` et `https://www.bcm.mr` sont deux origines distinctes pour le
navigateur : à l'étape 4, les deux devront figurer dans `CORS_ALLOWED_ORIGINS`
si le site répond sur les deux.

## 4. Mettre à jour le corpus

L'index est construit dans l'image. Pour ajouter une Lettre d'information :

```bash
python scripts/fetch_lettres_information.py --year 2026
python scripts/ocr_lettres_information.py      # macOS uniquement
git add data/lettres_information && git commit && git push
```

Railway reconstruit et redéploie. L'OCR reste une étape locale sur macOS ; ses
fichiers texte sont versionnés, et Railway n'a jamais besoin du moteur OCR.

`POST /api/reindex` existe mais reconstruit dans un conteneur éphémère : son
effet disparaît au prochain redémarrage. Il dépanne, il ne remplace pas un
redéploiement.

## 5. Limites connues

- **Analyse des graphiques désactivée.** Elle repose sur Swift et Apple Vision,
  disponibles seulement sur macOS. `CHART_ANALYSIS_ENABLED=false` en production
  tant qu'un moteur OCR multiplateforme ne l'a pas remplacée.
- **Une seule réplique.** Voir la note sur la mémoire et le limiteur de débit.
- **Pas de journalisation centralisée.** Les journaux restent dans Railway.
