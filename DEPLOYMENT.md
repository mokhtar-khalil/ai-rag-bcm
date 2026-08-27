# Déploiement — référence unique

Ce document résume tout ce qui a été mis en place pour le déploiement du
chatbot RAG BCM : quel fichier fait quoi, et les commandes exactes pour
chaque scénario (dev local, Docker local, VM de test, futur serveur BCM).
Objectif : ne plus avoir à redemander « comment on fait déjà » — tout est ici.

## 0. Déploiement hébergé (Railway + Vercel) — voie retenue

Deux documents dédiés ont été écrits pour cette voie et font désormais référence :

- **`docs/DEPLOIEMENT_RAILWAY_VERCEL.md`** — mise en service de l'API sur
  Railway et du widget sur Vercel : variables, dimensionnement, vérifications,
  mise à jour du corpus.
- **`docs/INTEGRATION_EQUIPE_BCM.md`** — à transmettre à l'équipe du site
  bcm.mr : une balise `<script>`, et la seule information qu'elle doit nous
  fournir en retour (la liste exacte de ses origines).

Le reste de ce document décrit les scénarios locaux, Docker et VM, qui restent
valables pour le développement et les essais.

## 1. Vue d'ensemble

```
┌─────────────────┐        ┌──────────────────────┐        ┌─────────────────┐
│  Widget web      │  HTTP  │   API Flask           │  lit   │  data/*.pdf      │
│  (widget/)        │ ─────▶ │   (api/, core/)        │ ─────▶ │  storage/*.joblib│
│  intégré au site  │  CORS  │   Gunicorn en prod     │        │  (index construit│
│  bcm.mr (Phase 3)  │        │   (Phases 0-1)          │        │   au build Docker)│
└─────────────────┘        └──────────────────────┘        └─────────────────┘
                                    ▲
                                    │ (usage interne only,
                            ┌───────┴────────┐   jamais public — Phase 3 Option B)
                            │  Gradio          │
                            │  (frontend/)      │
                            └─────────────────┘
```

- **`api/` + `core/`** : le cerveau (recherche + génération de réponse). C'est
  la seule chose qui doit tourner en production.
- **`widget/`** : l'interface publique, à héberger sur le site bcm.mr. Appelle
  l'API en HTTP/CORS, ne contient aucune logique métier.
- **`frontend/`** (Gradio) : outil de démo **interne uniquement**, jamais
  exposé publiquement (décision Phase 3, option B).
- **`Dockerfile` + `docker-compose.yml`** : empaquettent uniquement `api/` +
  `core/` (pas Gradio, pas le widget) pour un déploiement reproductible.
- **`.github/workflows/`** : automatisent test + build + publication de
  l'image à chaque changement de code sur GitHub.

## 2. Inventaire des fichiers — qui fait quoi

| Fichier / dossier | Rôle | Modifié à la main ? |
|---|---|---|
| `core/config.py` | Lit toutes les variables d'environnement, les valide. Source de vérité de la config. | Non (sauf ajout d'un nouveau réglage) |
| `api/app.py` | Routes Flask (`/health`, `/api/ask`, `/api/reindex`), CORS, rate limiting. | Non |
| `api/providers.py` | Appelle OpenAI, Gemini, Ollama, ou le mode extractif selon `GENERATION_PROVIDER`. | Non |
| `.env` | Config **locale** (dev), jamais committée. Contient tes vraies clés API. | **Oui**, c'est toi qui l'édites |
| `.env.example` | Modèle de `.env`, committé, sans valeurs secrètes. | Référence uniquement |
| `.env.production` | Config **production/VM**, jamais committée, créée à partir du modèle ci-dessous. | **Oui**, sur le serveur/VM cible |
| `.env.production.example` | Modèle de `.env.production`, committé. | Référence uniquement |
| `Dockerfile` | Recette de l'image Docker de l'API (Python + dépendances + index pré-construit). | Non |
| `docker-compose.yml` | Comment lancer le conteneur (port, fichier d'env, healthcheck). | Non |
| `requirements-api.txt` | Dépendances Python de l'API seule (utilisé par Docker). | Non |
| `requirements.txt` | Dépendances complètes (API + Gradio), pour le dev local (`setup.sh`). | Non |
| `run.sh` | Lance l'API + Gradio en local (dev), sans Docker. | Non |
| `run_api_prod.sh` | Lance l'API seule via Gunicorn (utilisé par le Dockerfile ET en local si besoin). | Non |
| `setup.sh` | Installe l'environnement Python local (`.venv`) pour le dev. | Non |
| `widget/bcm-chat-widget.js` | Le widget public complet (JS pur, sans dépendance). | Oui si tu veux changer le design/textes |
| `widget/demo.html` | Page de test locale du widget (simule une page du site BCM). | Oui pour changer l'URL de l'API testée |
| `widget/README.md` | Guide d'intégration pour l'équipe dev BCM. | Référence |
| `.github/workflows/ci.yml` | Tests + lint + audit sécurité + build Docker à chaque push/PR. | Non |
| `.github/workflows/cd.yml` | Publie l'image Docker sur GitHub Container Registry à chaque merge sur `main`. | Non |
| `.github/workflows/promote.yml` | Promotion manuelle d'une image vers `:production` (déclenchement humain uniquement). | Non |
| `~/.ssh/bcm_vm_key` (hors repo) | Clé SSH pour se connecter à la VM de test VirtualBox. | Ne pas committer |
| `~/.ssh/bcm_vm_password.txt` (hors repo) | Mot de passe de secours de la VM de test. | Ne pas committer |

## 3. Commandes par scénario

### A. Dev local, sans Docker (le plus rapide pour coder)

```bash
cd ~/Desktop/bcm_rag_chatbot
./run.sh
```
Lance l'API (port 5000) + Gradio (port 7861). Relit `.env` à chaque démarrage
— **si tu modifies `.env`, il faut relancer** (`Ctrl+C` puis `./run.sh`).

### B. Docker en local (pour tester exactement ce qui sera déployé)

```bash
cd ~/Desktop/bcm_rag_chatbot
cp .env.production.example .env.production   # une seule fois, puis éditer
docker compose up --build -d
docker compose ps                              # doit afficher "healthy"
curl http://127.0.0.1:5000/health
docker compose logs -f                         # suivre les logs
docker compose down                             # arrêter
```

### C. VM de test VirtualBox (simulateur de serveur BCM)

```bash
# 1. Démarrer la VM
VBoxManage startvm bcm_ai_assistant --type headless
ssh -i ~/.ssh/bcm_vm_key -p 2222 bcmops@127.0.0.1 "hostname"   # vérifier l'accès

# 2. Envoyer le code à jour
cd ~/Desktop/bcm_rag_chatbot
rsync -az -e "ssh -i ~/.ssh/bcm_vm_key -p 2222" \
  --exclude='.venv' --exclude='.git' --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='storage/models' --exclude='storage/chart_pages' --exclude='storage/chart_ocr' \
  --exclude='logs' --exclude='.env' --exclude='.env.production' --exclude='frontend' \
  --exclude='docs' --exclude='*.md' --exclude='evaluation' --exclude='.ruff_cache' \
  --exclude='.claude' \
  ./ bcmops@127.0.0.1:~/bcm_rag_chatbot/

# 3. Se connecter et (re)construire
ssh -i ~/.ssh/bcm_vm_key -p 2222 bcmops@127.0.0.1
cd ~/bcm_rag_chatbot
sudo docker compose down
sudo docker compose up --build -d
sudo docker compose ps        # attendre "healthy"
exit

# 4. Éteindre proprement à la fin
VBoxManage controlvm bcm_ai_assistant poweroff
```

**Deux pièges déjà rencontrés, à vérifier systématiquement :**

| Piège | Symptôme | Vérification | Correction |
|---|---|---|---|
| Port 5000 pris par `./run.sh` local | Les tests semblent fonctionner mais donnent des résultats bizarres/incohérents | `lsof -nP -iTCP:5000 -sTCP:LISTEN` — si ça montre un process `python`, conflit confirmé | Rediriger la VM sur un autre port : `VBoxManage controlvm bcm_ai_assistant natpf1 delete api` puis `VBoxManage controlvm bcm_ai_assistant natpf1 "api,tcp,127.0.0.1,5001,,5000"` — utiliser ensuite `:5001` |
| Cache du navigateur après modif de `demo.html` | Les changements ne semblent jamais pris en compte | Vérifier dans les DevTools réseau vers quel port part vraiment la requête | Recharger avec `?v=2` ajouté à l'URL, ou Cmd+Maj+R |
| Manque de RAM pendant le build (VM à 2 Go) | `exit code: 137` pendant la construction de l'index | `sudo dmesg \| grep -i oom` sur la VM | Swap déjà ajouté (`/swapfile`, 2 Go) — si ça replante, augmenter la RAM de la VM (`VBoxManage modifyvm bcm_ai_assistant --memory 4096`, VM éteinte) |

### D. Tester le widget contre l'API (local, Docker, ou VM)

```bash
# Éditer widget/demo.html pour pointer data-api-url vers la bonne adresse
# (127.0.0.1:5000 en local/Docker, 127.0.0.1:5001 si VM redirigée)

cd ~/Desktop/bcm_rag_chatbot/widget
python3 -m http.server 8090
# puis ouvrir http://127.0.0.1:8090/demo.html
```

N'oublie pas : `CORS_ALLOWED_ORIGINS` dans le `.env`/`.env.production` de
l'API testée doit inclure `http://127.0.0.1:8090`, sinon le widget affichera
« Service indisponible ».

### E. CI/CD (automatique, rien à taper)

| Événement | Ce qui se déclenche | Fichier |
|---|---|---|
| `git push` ou Pull Request sur `main` | Tests + lint + audit sécurité + build Docker (validation) | `.github/workflows/ci.yml` |
| Merge sur `main` | Build + publication de l'image sur GitHub Container Registry, tags `:<sha>` et `:staging` | `.github/workflows/cd.yml` |
| Déclenchement manuel sur GitHub (bouton « Run workflow ») | Retag d'une image déjà publiée vers `:production` | `.github/workflows/promote.yml` |

### F. Futur serveur BCM réel

Voir la section dédiée déjà donnée précédemment (cas serveur vierge vs
serveur avec site existant) — les commandes sont les mêmes qu'en `B.` et `C.`
ci-dessus, remplacer simplement l'hôte SSH par celui du vrai serveur BCM.
Point clé si un site existe déjà : garder `API_HOST=127.0.0.1` (pas
`0.0.0.0`) et passer par un reverse proxy nginx dédié à un sous-domaine, pour
ne jamais toucher au site existant.

## 4. Où sont les secrets

| Secret | Où il vit | Committé ? |
|---|---|---|
| Clé OpenAI / Gemini | `.env` (local) ou `.env.production` (VM/serveur) | Jamais |
| Jeton de réindexation (`REINDEX_TOKEN`) | `.env.production` | Jamais |
| Clé SSH de la VM de test | `~/.ssh/bcm_vm_key` | Jamais (hors du dossier projet) |
| Mot de passe de la VM de test | `~/.ssh/bcm_vm_password.txt` | Jamais |

`.env`, `.env.production` et tout `.ssh/` sont exclus par `.gitignore` — rien
de tout ça ne peut partir sur GitHub par erreur (vérifié par `gitleaks` en CI
en plus, par sécurité).

## 5. Statut actuel (à mettre à jour au fil du temps)

- Phases 0 à 3 du roadmap d'industrialisation : faites et testées.
- Rien n'est encore committé/poussé sur GitHub à ce stade — à faire quand tu
  seras prêt (`git add`, `git commit`, `git push`).
- Accès au vrai serveur BCM : pas encore obtenu. La VM VirtualBox sert de
  répétition générale en attendant.
- Phase 4 (scalabilité), Phase 5 (observabilité), Phase 6 (gouvernance des
  données) : pas encore commencées.
