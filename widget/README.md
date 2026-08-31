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
| `data-title-fr` / `data-title-ar` | "Assistant des publications de la BCM" / "مساعد منشورات البنك المركزي" | Titre affiché dans l'en-tête du panneau. |
| `data-logo-url` | — | Logo officiel affiché dans l'en-tête et sur l'avatar des réponses. À défaut, une marque intégrée est utilisée. |
| `data-streaming` | `true` | Affichage de la réponse au fil de sa rédaction. `false` force l'appel unique à `/api/ask`. |

### Logo

Sans `data-logo-url`, le widget affiche une marque vectorielle intégrée (fronton
et colonnes) : elle se lit comme « institution monétaire » et n'imite aucun logo
existant. Pour afficher le logo officiel de la BCM :

```html
<script
  src="https://bcm.mr/widget/bcm-chat-widget.js"
  data-api-url="https://bcm.mr"
  data-logo-url="/imgs/logo_banque_centrale_mauritanie.webp"
></script>
```

Servez le fichier depuis le même domaine que la page : une image hébergée
ailleurs peut être bloquée par la politique de sécurité du site. L'image est
cadrée sans déformation, quelle que soit sa forme d'origine.

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

> **`localhost` et `127.0.0.1` sont deux origines différentes pour le
> navigateur.** Ouvrir la démo sur `http://localhost:8090` alors que
> `CORS_ALLOWED_ORIGINS` ne cite que `http://127.0.0.1:8090` fait échouer tous
> les appels : le widget reste muet et la console affiche
> `TypeError: Failed to fetch`. Même effet en ouvrant `demo.html` par
> double-clic (`file://`), qui envoie l'origine `null`. Le `.env` de
> développement autorise donc les deux formes.

Si le widget ne répond pas, ouvrir la console du navigateur : une erreur CORS y
est toujours visible, et elle nomme l'origine exacte à ajouter.

### Choix de la langue

L'en-tête présente les deux langues côte à côte, `FR` et `AR`, la langue active
portant une pastille blanche pleine et les deux autres restant estompées sans
fond. Le nom complet — « Français », « العربية » — reste disponible en infobulle
et pour les lecteurs d'écran, et `aria-pressed` porte l'état.

Ce choix corrige deux défauts. Un bouton unique affichant « AR » ne dit pas s'il
nomme la langue courante ou la langue cible. Et les noms complets occupaient
113 px des 392 px de l'en-tête, ce qui repoussait l'état de l'assistant sur une
seconde ligne ; les codes en occupent 67.

Basculer en arabe fait passer tout le panneau en lecture droite-à-gauche : c'est
le signal le plus fort du mode actif, la pastille ne servant qu'au moment de
choisir.

### Consentement, journalisation et limite de session

Avant la première question d'une session, un popup demande l'accord de
l'utilisateur pour conserver sa question et la réponse obtenue, à des fins
d'analyse. Ce choix ne se redemande pas à chaque message : il vaut pour toute
la session, et seulement pour elle.

- **Accepté** : chaque question de la session est journalisée côté serveur —
  texte de la question, texte de la réponse, langue, horodatage. Jamais
  d'adresse IP ni d'identifiant permettant de reconnaître la personne.
- **Refusé** : l'assistant reste pleinement utilisable, rien n'est conservé.

Une session est limitée à un nombre de questions (`SESSION_MAX_QUESTIONS`
côté API, 10 par défaut) et se réinitialise — nouveau consentement demandé,
compteur remis à zéro — après un délai d'inactivité
(`SESSION_IDLE_MINUTES`, 30 minutes par défaut). Cette réinitialisation
survient même dans un onglet resté ouvert : elle ne dépend pas de la fermeture
du navigateur. Le paramètre `data-session-idle-minutes` (ou
`sessionIdleMinutes` dans `window.BCM_CHAT_CONFIG`) doit rester cohérent avec
la valeur côté serveur ; il ne sert qu'à redemander le consentement au bon
moment, le serveur restant seul décisionnaire du quota réel.

Au-delà de la limite, l'assistant répond avec un message explicite dans la
conversation elle-même (pas une alerte du navigateur), indiquant après combien
de temps une nouvelle session sera possible.

### Réponse progressive

Le widget appelle `/api/ask/stream` et affiche la réponse pendant sa rédaction,
précédée du nom de l'étape en cours — plusieurs secondes séparent la question du
premier mot, le temps de chercher et de sélectionner les passages.

Le texte affiché pendant la diffusion est **provisoire** : il est remplacé par la
réponse validée à réception de l'événement `done`. Si le flux échoue — proxy qui
le tamponne, navigateur ancien, réseau d'entreprise — le widget bascule
automatiquement sur `/api/ask` en un seul appel. `data-streaming="false"` force
ce mode.

## Ce que fait (et ne fait pas) le widget

- Conversation en mémoire de session (`sessionStorage`) : elle survit à un
  rechargement de page mais n'est pas partagée entre onglets ni utilisateurs,
  comme l'interface Gradio actuelle.
- Rend les réponses en markdown léger : **gras**, *italique*, listes à puces
  (`-`, `*`, `•`, `+`), listes numérotées et titres. Les modèles alternent ces
  conventions d'une réponse à l'autre ; n'en reconnaître qu'une laissait le
  marqueur brut visible au début de chaque puce.
- Affiche les citations
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
