#!/bin/sh
# Prépare le dossier publié par Vercel.
#
# Vercel ne sert que des fichiers statiques : aucune variable d'environnement
# n'est lisible à l'exécution. Celles définies dans le projet sont en revanche
# disponibles pendant la construction, ce qui permet d'inscrire l'URL de l'API
# dans la page de démonstration sans la figer dans le dépôt.
#
# Seule variable utilisée : BCM_API_URL. Si elle est absente, la page conserve
# son comportement local et reste utilisable via « ?api=https://… ».
set -eu

# Les chemins sont résolus depuis l'emplacement du script, jamais depuis le
# répertoire courant : la plateforme de déploiement choisit ce dernier, et une
# racine de projet mal réglée ferait échouer une résolution relative.
RACINE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SOURCE="$RACINE/widget"
SORTIE="$RACINE/public"

if [ ! -d "$SOURCE" ]; then
  echo "Dossier introuvable : $SOURCE" >&2
  echo "Le script doit rester dans scripts/, à la racine du dépôt." >&2
  exit 1
fi

rm -rf "$SORTIE"
mkdir -p "$SORTIE"
cp "$SOURCE/bcm-chat-widget.js" "$SORTIE/"
cp "$SOURCE/demo.html" "$SORTIE/"

if [ -n "${BCM_API_URL:-}" ]; then
  # Le slash final casserait les URL construites par concaténation.
  PROPRE=$(printf '%s' "$BCM_API_URL" | sed 's:/*$::')
  case "$PROPRE" in
    https://*|http://127.0.0.1:*|http://localhost:*) ;;
    *)
      echo "BCM_API_URL doit être une URL https (ou une adresse locale) : $PROPRE" >&2
      exit 1
      ;;
  esac
  # Le séparateur « | » évite d'échapper les slashes de l'URL.
  sed "s|__BCM_API_URL__|$PROPRE|g" "$SOURCE/demo.html" > "$SORTIE/demo.html"
  echo "Démonstration pointée vers $PROPRE"
else
  echo "BCM_API_URL non définie : la démonstration vise l'API locale."
fi

echo "Publié : $(ls "$SORTIE" | tr '\n' ' ')"
