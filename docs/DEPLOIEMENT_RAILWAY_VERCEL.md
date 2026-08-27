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
| `CORS_ALLOWED_ORIGINS` | `https://www.bcm.mr,https://bcm.mr,https://<projet>.vercel.app` | |
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

### Diffusion des réponses

L'API renvoie la réponse en Server-Sent Events sur `/api/ask/stream`. La
réponse porte `X-Accel-Buffering: no`, indispensable derrière un proxy qui
tamponnerait sinon le flux et annulerait tout son bénéfice. Le widget bascule
seul sur l'appel unique `/api/ask` si le flux échoue.

### Poids et architecture de l'image

Mesuré sur l'image construite :

| Composant | Taille |
|---|---|
| Dépendances Python (torch, sentence-transformers) | 1,3 Go |
| `storage/` (index et modèle d'embedding) | 487 Mo |
| `data/` (rapport PDF et Lettres) | 80 Mo |
| **Total transféré** | **≈ 1,2 Go** |

Laissez **Railway construire l'image depuis le dépôt**. Une image construite sur
un Mac Apple Silicon est en `linux/arm64` et ne démarrerait pas sur
l'infrastructure de Railway, qui est en `amd64`.

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

`vercel.json` publie le dossier `widget/` en statique. Aucune construction,
aucune fonction serveur.

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
curl -sI https://<projet>.vercel.app/bcm-chat-widget.js | grep -i "content-type\|cache-control\|access-control"
```

Puis ouvrir `https://<projet>.vercel.app/demo.html` : la page de démonstration
sert de banc d'essai indépendant du site de la BCM.

> `demo.html` porte une `data-api-url` locale (`http://127.0.0.1:5000`). Avant
> de publier, pointez-la vers l'URL Railway, sinon la démonstration en ligne
> reste muette.

## 3. Ordre des opérations

1. Déployer l'API sur Railway ; noter son URL.
2. Renseigner `CORS_ALLOWED_ORIGINS` avec les domaines de la BCM **et** le
   domaine Vercel.
3. Pointer `data-api-url` de `widget/demo.html` vers l'URL Railway.
4. Déployer le widget sur Vercel ; noter son URL.
5. Transmettre à l'équipe BCM `docs/INTEGRATION_EQUIPE_BCM.md`, complété des
   deux URL.
6. Leur demander la liste exacte de leurs origines, et l'ajouter à
   `CORS_ALLOWED_ORIGINS`.

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
