# Résultats — jeu de test « Lettres d'information 2026 »

Exécution du 26 août 2026, chaîne complète via `POST /api/ask`.
Fournisseur `openai` / `gpt-5.6-terra`, reranking `gpt-5.6-luna`.
71 générations, **aucun échec fournisseur, aucun repli extractif** : les chiffres
portent bien sur le modèle et non sur le mode dégradé. Les 5 questions restantes
n'ont pas atteint l'étape de génération, la chaîne ayant refusé en amont.

Réponses brutes : `resultats_lettres_2026_openai.json`.

## Questions ayant une réponse dans le corpus (70)

| catégorie | n | source correcte | réponse complète | partielle | ratée |
|---|---:|---:|---:|---:|---:|
| direct | 42 | 98 % | 95 % | 0 % | 5 % |
| paraphrase | 5 | 100 % | 100 % | 0 % | 0 % |
| liste | 9 | 100 % | 78 % | 22 % | 0 % |
| raisonnement | 14 | 100 % | 71 % | 29 % | 0 % |
| **total** | **70** | **99 %** | **89 %** | **9 %** | **3 %** |

Latence médiane 6,8 s, maximum 19,0 s.

## Questions sans réponse (6)

Cinq refus nets. Le sixième n'est pas un échec : interrogée sur la croissance
2027, la chaîne a cité la fourchette 2025-2027 du Rapport annuel **et précisé**
qu'aucun taux distinct pour 2027 n'y figure. Le piège était mal calibré, la
réponse est bonne.

**Aucune invention constatée sur les 76 cas.** Quand le passage manque, le
modèle le dit au lieu de combler.

## Les huit réponses imparfaites

| cas | nature |
|---|---|
| `li2607_08` | Faux négatif de notation : la réponse dit « de 900 à 450 points de base », le grader cherchait « 900 points de base ». |
| `licross_01` | Idem : le niveau de départ 6,00 % précède la fenêtre mai-juillet demandée. |
| `li2606_10` | Le Gouverneur de la Banque de France est désigné par « Il », jamais nommé. |
| `li2606_08` | Le 4ᵉ axe de coopération est déformé : « assistance technique pour les systèmes de paiement » au lieu de « en prévision économique et systèmes de paiement ». Texte source issu d'une infographie, OCR dégradé. |
| `licross_06` | Couvre HCR, PAM et Women in Finance, omet HIMAYA (juin p. 5). |
| `licross_03` | Donne 2,65 Md USD (juillet) mais annonce que mai ne chiffre rien : le retrieval a servi mai p. 2, le chiffre est p. 1. |
| `li2606_07` | PAMIF I et II sont juin p. 3 ; la génération a reçu p. 2. Le modèle signale correctement l'absence. |
| `li2607_13` | **Bug** : la question part sur la voie d'analyse graphique et répond sur un graphique de taux du Rapport annuel p. 28, hors sujet, alors que la bonne page (juillet p. 12) figure dans les sources. |

## Ce qu'il faut retenir

1. **Le garde-fou utile est le modèle, pas `is_relevant()`.** En retrieval seul,
   les 6 questions sans réponse franchissaient toutes le seuil
   `MIN_RELEVANCE_SCORE=0,075` avec des scores de 0,44 à 0,50. C'est l'étape de
   génération qui rattrape. Le seuil ne protège de rien en pratique.
2. **Trouver n'est pas transmettre.** Le retrieval place la bonne page dans le
   top-8 dans 99 % des cas, mais `li2606_07`, `licross_03` et `licross_06`
   échouent parce que la bonne page est écartée à la sélection des passages
   envoyés au générateur. C'est là qu'il faut chercher des gains, pas dans le
   rappel.
3. **Les questions multi-documents plafonnent.** 71 % de réponses complètes en
   catégorie raisonnement : les réponses sont exactes mais incomplètes, une
   édition sur deux ou trois étant oubliée.
4. **La voie graphique se déclenche à tort.** Un cas sur 76, mais la réponse
   produite est entièrement hors sujet.
5. **La latence est élevée** pour un widget conversationnel : 6,8 s en médiane.
