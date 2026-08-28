# Intégrer l'assistant BCM sur bcm.mr

Destinataire : équipe de développement du site bcm.mr
Effort côté site : **une balise `<script>`**, aucune dépendance, aucune étape de build.

## 1. Ce qu'il faut ajouter

Avant la fermeture de `</body>`, sur les pages où l'assistant doit apparaître :

```html
<script
  src="https://ai-bcm.vercel.app/bcm-chat-widget.js"
  data-api-url="https://assistant-bcm.up.railway.app"
  data-logo-url="/imgs/logo_banque_centrale_mauritanie.webp"
></script>
```

Remplacez les deux premières URL par celles que l'équipe qui héberge le service
vous communiquera : elles sont fixées au déploiement.

Le widget crée lui-même sa bulle flottante et son panneau. Il est isolé du CSS
du site par un Shadow DOM : **il ne peut ni lire ni modifier le contenu de la
page**, et le style du site ne peut pas le déformer.

## 2. Ce que nous devons recevoir de vous

Une seule information : **la liste exacte des origines** depuis lesquelles le
widget sera chargé. Le navigateur bloque les appels inter-origines non
autorisés, et l'autorisation se règle de notre côté.

Une origine, c'est le protocole, le domaine et le port — sans chemin :

```text
https://www.bcm.mr
https://bcm.mr
https://preprod.bcm.mr      ← si vous testez sur un environnement séparé
```

Attention : `https://bcm.mr` et `https://www.bcm.mr` sont **deux origines
distinctes** pour le navigateur. Si le site répond sur les deux, indiquez-les
toutes les deux. Il en va de même pour `http://localhost:3000` en développement.

Sans cette autorisation, le widget s'affiche mais reste muet, et la console du
navigateur signale une erreur CORS.

## 3. Rien à configurer côté serveur

Le widget est un fichier statique. Il ne lit aucune variable d'environnement et
n'exige aucune modification de votre configuration serveur, de votre `next.config`
ou de vos variables de build. Tout se règle par les attributs `data-*` de la
balise, lisibles dans le HTML.

## 4. Options disponibles

Toutes facultatives, en attributs `data-*` sur la balise `<script>` :

| Attribut | Défaut | Description |
|---|---|---|
| `data-api-url` | — (obligatoire) | URL de l'API, sans slash final. |
| `data-logo-url` | — | Logo officiel affiché dans l'en-tête et sur l'avatar. |
| `data-language` | `fr` | Langue initiale : `fr` ou `ar`. L'utilisateur peut basculer. |
| `data-position` | `bottom-right` | `bottom-right` ou `bottom-left`. |
| `data-accent-color` | `#0f766e` | Couleur d'accent, à aligner sur la charte BCM. |
| `data-title-fr` / `data-title-ar` | « Assistant des publications de la BCM » | Titre du panneau. |
| `data-streaming` | `true` | Réponse affichée au fil de sa rédaction. |

Exemple avec la charte du site :

```html
<script
  src="https://ai-bcm.vercel.app/bcm-chat-widget.js"
  data-api-url="https://assistant-bcm.up.railway.app"
  data-logo-url="/imgs/logo_banque_centrale_mauritanie.webp"
  data-accent-color="#0a3d62"
  data-language="fr"
></script>
```

## 5. Ce que l'assistant sait et ne sait pas

Il répond **uniquement** à partir des documents publiés par la BCM qui sont
indexés : le Rapport annuel de l'exercice 2025 et les Lettres d'information
mensuelles 2026. Chaque réponse cite sa source — page du rapport, ou lettre avec
un lien vers la page publique correspondante.

Toute question sortant de ce corpus reçoit un refus explicite. L'assistant
n'invente pas de chiffre et ne complète pas avec des connaissances générales.

Il ne traite **pas** les demandes individuelles : réclamation d'un client,
demande d'agrément, question juridique personnelle. Prévoyez un lien de contact
humain à proximité.

## 6. Vérifier que l'intégration fonctionne

1. Ouvrir une page du site qui porte la balise.
2. La bulle apparaît en bas à droite ; cliquer dessus.
3. L'en-tête doit afficher un point vert et « Assistant documentaire prêt ».
   Un point rouge signifie que l'API n'est pas joignable — le plus souvent une
   origine non autorisée.
4. Poser : « Quel a été le taux de croissance du PIB réel en 2025 ? »
   La réponse doit s'écrire progressivement et citer `p. 21`.

En cas de silence, ouvrir la console du navigateur : une erreur CORS y nomme
l'origine exacte à nous transmettre.

## 7. Vie privée

Le widget conserve la conversation dans le `sessionStorage` du navigateur : elle
survit à un rechargement, n'est pas partagée entre onglets ni entre visiteurs, et
disparaît à la fermeture. Aucun cookie n'est déposé. Aucune donnée n'est envoyée
à un tiers : le widget n'appelle que l'URL indiquée dans `data-api-url`.

Les questions transitent par l'API et, pour la rédaction de la réponse, par le
fournisseur de génération configuré. À signaler dans votre politique de
confidentialité si vous en tenez une.
