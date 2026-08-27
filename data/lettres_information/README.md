# Lettres d'information de la BCM — éditions 2026

Sept PDF, un par édition mensuelle de janvier à juillet 2026.

## Provenance

Ces PDF ne sont pas des fichiers publiés par la BCM : **la Banque ne diffuse pas
ces lettres au format PDF**. Chaque édition est mise en ligne comme une image
unique — un bandeau vertical (jusqu'à 2481 × 42096 px) ou une page A4 — attachée
à une actualité du back-office Drupal `bo.bcm.mr`, taguée `lettre_d_information`.

`scripts/fetch_lettres_information.py` interroge l'API JSON du site, télécharge
l'image d'origine et la redécoupe en pages A4 sans rien réécrire : le contenu
est celui publié par la BCM, pixel pour pixel. Le fichier `manifest.json` donne
pour chaque édition son URL publique, l'URL de l'image source, ses dimensions et
sa date de mise en ligne.

## Régénérer

```bash
python scripts/fetch_lettres_information.py --year 2026
```

Les images téléchargées sont conservées dans `.cache/` (non versionné) : une
seconde exécution ne recharge rien. Options utiles :

- `--year 2025` — les éditions antérieures, dont celle de **décembre 2025**,
  publiée le 5 janvier 2026 ;
- `--lang ar` — les versions arabes, écrites dans `ar/` ;
- `--all` — toutes les éditions disponibles, sans filtre d'année.

## Limites connues

- **Pas de couche texte.** Les pages sont des images ; l'index lexical n'en
  tirera aucun mot en l'état. Une passe OCR est nécessaire avant indexation
  (le binaire `storage/chart_ocr`, Apple Vision, gère déjà le français).
- **Janvier et février 2026** ne comptent qu'une page : c'est tout ce que la BCM
  a mis en ligne pour ces deux mois. Les éditions à partir de mars sont des
  bandeaux complets, d'où leur pagination plus fournie.
- Les numéros de page et mentions de mois imprimés **dans** les images sont ceux
  de la BCM, coquilles comprises (l'édition de juillet 2026 porte « Juillet 2025 »
  en pied de page).
- La découpe cherche la bande de pixels la plus uniforme autour de chaque coupe
  théorique pour ne pas trancher une ligne de texte, mais un article peut
  malgré tout se poursuivre d'une page à la suivante.
