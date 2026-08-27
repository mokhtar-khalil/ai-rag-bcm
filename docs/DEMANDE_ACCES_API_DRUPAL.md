# Demande d'accès à l'API de contenu du site bcm.mr

Destinataire : équipe web / éditeur du site bcm.mr
Objet : accès en lecture au contenu Drupal pour l'assistant documentaire BCM
Contexte : le site est une SPA React (Drupal 11 en back-office, proxy Node/Express).
Le HTML servi par `https://www.bcm.mr/` ne contient aucun contenu éditorial ; une
récupération par le HTML public n'est donc ni fiable ni maintenable.

## Ce qui est demandé

Un accès **en lecture seule** au contenu déjà public du site. Aucun accès en
écriture, aucun contenu non publié, aucune donnée personnelle ou utilisateur.

Deux options, par ordre de préférence :

1. **JSON:API (module core Drupal)** — endpoints `GET /jsonapi/node/<type>`
   restreints aux types de contenu concernés.
2. **Views REST export** — une vue par type de contenu, exposée en JSON, si
   l'ouverture de JSON:API n'est pas souhaitée.

## Types de contenu concernés (version 1)

Uniquement les pages institutionnelles, soit les 83 URLs `/page/<slug>/<id>`
présentes dans `https://www.bcm.mr/sitemap.xml`.

Les actualités, communiqués, appels d'offres et données chiffrées (taux de
change, adjudications) feront l'objet d'une demande ultérieure.

## Champs nécessaires par élément

| Champ | Usage |
|---|---|
| `nid` / `uuid` | identifiant stable de déduplication |
| `title` | titre affiché et pondération de la recherche |
| `body` (texte rendu ou brut) | contenu indexé |
| `langcode` | séparation des contenus français et arabes |
| `status` | ne jamais indexer un contenu non publié |
| `changed` | réindexation incrémentale : ne retraiter que ce qui a changé |
| `created` | datation de l'information dans la réponse |
| `path alias` ou slug | reconstruction de l'URL publique citée à l'utilisateur |

## Éléments d'exploitation à confirmer

- **Authentification** : clé d'API ou compte de service dédié à cet usage
  (préférable à un accès anonyme, pour la traçabilité et la révocation).
- **Filtre incrémental** : possibilité de filtrer sur `changed`
  (ex. `?filter[changed][value]=<timestamp>&filter[changed][operator]=>`).
- **Pagination** : taille de page maximale autorisée.
- **Limite de débit** : le crawl est prévu **une fois par jour, hors heures
  ouvrées**, avec un `User-Agent` identifiable. Merci d'indiquer toute limite
  à respecter.
- **Réseau** : confirmer que l'hôte de l'API est joignable depuis le serveur
  qui hébergera l'assistant (`bo.bcm.mr` renvoie actuellement une erreur 500
  depuis l'extérieur ; `ww2.bcm.mr` est référencé dans `robots.txt`).
- **URL canonique** : confirmer que le motif public est bien
  `https://www.bcm.mr/page/<slug>/<nid>`, afin que les réponses de l'assistant
  citent un lien cliquable exact.

## Repli si l'accès n'est pas possible

Récupération par navigateur sans interface (rendu JavaScript) sur les URLs du
sitemap. Cette solution fonctionne mais reste fragile à toute évolution du
front, ne fournit ni date de mise à jour ni langue de manière fiable, et impose
de retraiter l'intégralité du site à chaque cycle au lieu des seules pages
modifiées. Elle est donc proposée uniquement comme solution d'attente.
