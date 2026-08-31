# Déploiement — API sur Railway, widget sur Vercel

Deux services, deux rôles :

```
Site bcm.mr  ──<script src=…>──►  Vercel   (fichier statique, widget)
     │
     └──────── appels XHR ───────►  Railway  (API Flask + index RAG)
```

Le site de la BCM n'héberge rien : il ajoute une balise `<script>`. Nous
hébergeons les deux services, ce qui nous laisse la main sur les mises à jour du
corpus et du modèle sans jamais toucher au site.

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
| `CORS_ALLOWED_ORIGINS` | `https://www.bcm.mr,https://bcm.mr,https://ai-bcm.vercel.app` | |
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

### Journalisation des questions et limite de session

Trois fonctionnalités liées, toutes soumises au consentement demandé par le
widget avant la première question d'une session :

- **Consentement** : un popup s'affiche une fois par session ; en cas de
  refus, l'assistant reste utilisable, rien n'est journalisé.
- **Limite anti-abus** : `SESSION_MAX_QUESTIONS` (10 par défaut) questions par
  session, réinitialisée après `SESSION_IDLE_MINUTES` (30 par défaut)
  d'inactivité. Distincte de `RATE_LIMIT_ASK`, qui freine un débit trop rapide
  plutôt qu'un volume total.
- **Journalisation** : question, réponse, langue et horodatage — jamais
  d'adresse IP ni d'identifiant permettant de reconnaître la personne.

#### Ajouter Postgres sur Railway

La journalisation exige une base **durable** : le disque d'un conteneur
Railway ne survit pas à un redéploiement sans volume attaché, et l'appli n'en
attache pas. Sans `DATABASE_URL`, un fichier SQLite local sert de repli —
correct en développement, mais silencieusement perdu au prochain déploiement
en production.

1. Dans le projet Railway : **New → Database → Add PostgreSQL**.
2. Railway injecte automatiquement `DATABASE_URL` dans les variables du
   service API — aucune valeur à copier à la main.
3. Au premier démarrage, l'application crée la table `logged_questions` si
   elle n'existe pas encore (best-effort : une base indisponible ne bloque
   pas le service, elle désactive silencieusement la journalisation).

Vérifier que la table existe :

```bash
railway connect postgres
```
```sql
\d logged_questions
SELECT count(*) FROM logged_questions;
```

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

## 2. Widget sur Vercel

`vercel.json` publie le widget en statique. Aucune fonction serveur, donc
**aucune variable d'environnement n'est lisible à l'exécution** : le widget se
configure par les attributs `data-*` de la balise `<script>`, côté site.

### La seule variable à définir

| Variable | Valeur | Obligatoire |
|---|---|---|
| `BCM_API_URL` | `https://<projet>.up.railway.app` | non |

Elle sert uniquement à la **page de démonstration**. Les variables du projet sont
disponibles pendant la construction : `scripts/build_widget_vercel.sh` y inscrit
l'URL de l'API, ce qui évite de figer une adresse d'environnement dans le dépôt.

Sans elle, la démonstration en ligne vise `127.0.0.1:5000` et reste muette — elle
demeure alors utilisable via `?api=https://…`. Le widget lui-même n'en dépend
jamais : les sites qui l'intègrent passent leur propre `data-api-url`.

Le script refuse une URL qui ne soit pas en `https` (ou en boucle locale) et
retire un éventuel slash final, qui casserait les URL construites par
concaténation.

Les en-têtes servis :

- `Access-Control-Allow-Origin: *` sur le fichier du widget. C'est le
  **script** qui devient chargeable depuis n'importe quel domaine, ce qui est
  nécessaire et sans risque : le contrôle d'accès réel se joue côté API, par
  `CORS_ALLOWED_ORIGINS`.
- `Cache-Control: public, max-age=300, stale-while-revalidate=86400`. Cinq
  minutes de cache : assez pour épargner le réseau, assez court pour qu'un
  correctif se propage sans intervention. Un cache long imposerait de renommer
  le fichier à chaque correction.

### Vérifier après déploiement

```bash
curl -sI https://ai-bcm.vercel.app/bcm-chat-widget.js | grep -i "content-type\|cache-control\|access-control"
```

Puis ouvrir la racine `https://ai-bcm.vercel.app/` : la démonstration y sert de
page d'accueil et de banc d'essai indépendant du site de la BCM. `cleanUrls`
étant actif, `/demo.html` redirige vers `/demo` — les deux fonctionnent.

> Sans `BCM_API_URL`, la démonstration vise `127.0.0.1`. Une page servie en
> HTTPS ne peut pas appeler une adresse locale en HTTP : le navigateur bloque la
> requête comme contenu mixte. Le widget l'annonce alors franchement — point
> rouge et « Service indisponible » — au lieu de rester silencieux.

## 3. Ordre des opérations

1. Déployer l'API sur Railway ; noter son URL.
2. Renseigner `CORS_ALLOWED_ORIGINS` avec les domaines de la BCM **et** le
   domaine Vercel.
3. Définir `BCM_API_URL` dans Vercel avec l'URL Railway, puis redéployer :
   la démonstration en ligne vise alors la bonne API.
4. Transmettre à l'équipe BCM `docs/INTEGRATION_EQUIPE_BCM.md`, complété des
   deux URL.
5. Leur demander la liste exacte de leurs origines, et l'ajouter à
   `CORS_ALLOWED_ORIGINS`.

L'ordre compte : le domaine Vercel doit figurer dans `CORS_ALLOWED_ORIGINS`
(étape 2) avant que la démonstration ne puisse interroger l'API.

L'étape 6 est celle qui bloque en pratique : `https://bcm.mr` et
`https://www.bcm.mr` sont deux origines distinctes pour le navigateur, et une
seule autorisée sur les deux laisse la moitié des visiteurs sans réponse.

## 4. Mettre à jour le corpus

L'index est construit dans l'image. Pour ajouter une Lettre d'information :

```bash
python scripts/fetch_lettres_information.py --year 2026
python scripts/ocr_lettres_information.py      # macOS uniquement
git add data/lettres_information && git commit && git push
```

Railway reconstruit et redéploie. L'OCR reste une étape locale sur macOS ; ses
fichiers texte sont versionnés, et ni Railway ni Vercel n'ont besoin du moteur
OCR.

`POST /api/reindex` existe mais reconstruit dans un conteneur éphémère : son
effet disparaît au prochain redémarrage. Il dépanne, il ne remplace pas un
redéploiement.

## 5. Limites connues

- **Analyse des graphiques désactivée.** Elle repose sur Swift et Apple Vision,
  disponibles seulement sur macOS. `CHART_ANALYSIS_ENABLED=false` en production
  tant qu'un moteur OCR multiplateforme ne l'a pas remplacée.
- **Une seule réplique.** Voir la note sur la mémoire et le limiteur de débit.
- **Pas de journalisation centralisée.** Les journaux restent dans Railway.
