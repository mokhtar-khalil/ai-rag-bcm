# Widget de chat BCM

Widget embarquable en une seule balise `<script>`, sans dépendance ni étape de
build. Il appelle directement l'API Flask du chatbot RAG (`/health` et
`/api/ask`) et affiche une bulle de conversation flottante sur la page hôte.

Gradio (`frontend/`) reste l'interface de démonstration interne ; ce widget
est la seule interface destinée à un usage public sur le site bcm.mr.

## Intégration côté site BCM

Ajouter avant la fermeture de `</body>` :

```html
<script
  src="https://rag.bcm.mr/widget/bcm-chat-widget.js"
  data-api-url="https://rag.bcm.mr"
></script>
```

Aucune autre modification n'est nécessaire : le widget crée sa propre bulle
flottante et son panneau de conversation, isolés du CSS du site (Shadow DOM).

## Options disponibles

Toutes facultatives, en attributs `data-*` sur la balise `<script>` :

| Attribut | Défaut | Description |
|---|---|---|
| `data-api-url` | — (obligatoire) | URL de base de l'API Flask, sans slash final. |
| `data-language` | `fr` | Langue initiale : `fr` ou `ar`. L'utilisateur peut basculer via le bouton dans l'en-tête. |
| `data-position` | `bottom-right` | `bottom-right` ou `bottom-left`. |
| `data-accent-color` | `#0f766e` | Couleur d'accent (bulle, boutons), à adapter à la charte BCM. |
| `data-title-fr` / `data-title-ar` | "Assistant BCM" / "مساعد البنك المركزي" | Titre affiché dans l'en-tête du panneau. |

Exemple avec la charte BCM et un panneau à gauche :

```html
<script
  src="https://rag.bcm.mr/widget/bcm-chat-widget.js"
  data-api-url="https://rag.bcm.mr"
  data-accent-color="#0a3d62"
  data-position="bottom-left"
></script>
```

## Pré-requis côté API

Le widget appelle l'API en cross-origin depuis le domaine du site BCM : la
variable `CORS_ALLOWED_ORIGINS` de `.env.production` (Phase 0) doit inclure ce
domaine exact, par exemple :

```dotenv
CORS_ALLOWED_ORIGINS=https://bcm.mr,https://www.bcm.mr
```

Sans cela, le navigateur bloquera les appels du widget (erreur CORS visible
dans la console du navigateur, le widget affichera « Service indisponible »).

## Tester en local

```bash
cd widget
python3 -m http.server 8090
```

Puis ouvrir `http://127.0.0.1:8090/demo.html` dans un navigateur, avec l'API
Flask locale démarrée (`./run.sh` ou le conteneur Docker) sur le port 5000.
`demo.html` simule une page quelconque du site BCM avec le widget intégré.

## Ce que fait (et ne fait pas) le widget

- Conversation en mémoire de session (`sessionStorage`) : elle survit à un
  rechargement de page mais n'est pas partagée entre onglets ni utilisateurs,
  comme l'interface Gradio actuelle.
- Rend les réponses en markdown léger (gras, listes) et affiche les citations
  `[p. PDF N]` sous forme de badges. Les pages sources sont regroupées dans un
  bloc « Sources » repliable, avec l'extrait concerné (pas de score de
  similarité affiché, volontairement retiré de l'interface).
- Gère les suggestions de clarification renvoyées par l'API (question
  ambiguë) sous forme de puces cliquables.
- Ne fait aucun appel à un service tiers : uniquement à l'API Flask indiquée
  via `data-api-url`.
- N'implémente pas l'analyse de graphiques en image (le widget affiche le
  texte de la réponse renvoyée par l'API, y compris quand `chart_analysis`
  est actif côté serveur).
