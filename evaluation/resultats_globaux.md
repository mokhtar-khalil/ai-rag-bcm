# Évaluation de bout en bout — corpus complet

Chaîne complète via `POST /api/ask` : recherche, planification, reranking et
génération. Fournisseur `openai` / `gpt-5.6-terra`, reranking `gpt-5.6-luna`.

`evaluate_retrieval.py` ne mesurait que la récupération — il dit si la bonne page
est retrouvée, jamais si la réponse est juste. Or tous les défauts constatés en
usage portaient sur les réponses. `scripts/evaluate_answers.py` comble ce vide et
s'applique aux deux jeux de test.

```bash
python scripts/evaluate_answers.py --dataset evaluation/questions.jsonl
python scripts/evaluate_answers.py --dataset evaluation/questions_lettres_2026.jsonl
```

## Résultats

| | Rapport annuel | Lettres d'information | Arabe |
|---|---:|---:|---:|
| Cas | 41 | 76 | 18 |
| dont répondables (avec faits attendus) | 29 | 70 | 16 |
| dont hors corpus | 6 | 6 | 2 |
| **Faits attendus tous restitués** | **93,1 %** | **90,0 %** | **93,8 %** |
| Réponse partielle | 3,4 % | 5,7 % | 0 % |
| Fait manqué | 3,4 % | 4,3 % | 6,3 % |
| Réponses fondées et citées | 100 % | 97,1 % | 100 % |
| Clarifications parasites | **0 %** | **0 %** | **0 %** |
| Refus corrects hors corpus | **100 %** | **100 %** | **100 %** |
| Page attendue citée | 93,1 % | 87,1 % | 75,0 % |
| Durée moyenne | 5,5 s | 9,0 s | 5,3 s |

Le jeu arabe (`evaluation/questions_arabe.jsonl`) couvre les mêmes faits que le
jeu français, plus deux questions sur les Lettres et deux hors corpus. Ses pages
attendues sont **dérivées du corpus** — recherche des passages contenant
réellement le fait — et non écrites à la main : mes premières valeurs étaient
fausses, ce qui affichait 56 % de pages correctes pour un comportement correct.

Aucun document n'est privilégié au détriment de l'autre : les deux se tiennent
à environ dix points au-dessus de quatre-vingts pour cent de restitution
complète, avec un garde-fou documentaire intact des deux côtés.

## L'arabe n'hallucinait pas : il lui manquait du vocabulaire

Une question arabe sur la part de la dette dans le PIB répondait que la valeur
était absente, alors que le français la trouvait page 60 au premier rang.

La cause n'était pas la génération mais l'enrichissement de la requête, qui
**aggravait** le problème : « الدين » (dette) n'avait aucune entrée au glossaire,
tandis que « الناتج المحلي » (PIB) en avait une qui ajoutait le vocabulaire de la
croissance. La recherche partait donc vers les pages de croissance, la dimension
dette ayant disparu.

Quatre familles d'entrées ont été ajoutées, chacune révélée par un échec mesuré :

| Vocabulaire arabe absent | Effet observé |
|---|---|
| dette, endettement, service de la dette | partait vers les pages de croissance |
| créances douteuses, qualité du portefeuille | renvoyait le total des actifs |
| solvabilité, fonds propres | recherche imprécise |
| compte courant, balance commerciale | refus alors que le fait existe |

Résultat : 87,5 % → **93,8 %** de faits restitués, au niveau du français. Le
français et les Lettres sont inchangés — vérifié après chaque ajout.

## Ce que cette campagne a corrigé

Deux défauts trouvés par l'évaluation elle-même, invisibles auparavant :

- **Clarifications parasites** : une question sur la norme IFRS 18 recevait
  « voulez-vous parler de l'indicateur *Pour les normes IFRS* ? ». Le filtre des
  libellés n'écartait que les verbes ; il écarte désormais aussi les fragments
  commençant par une préposition ou un déterminant. Taux ramené de 3,4 % à 0 %.
- **Réponses non fondées** : passées de 96,6 % à 100 % sur le rapport.

## Deux métriques étaient fausses, pas le système

- **Refus hors corpus** affichait 50 % sur les Lettres. Les trois « échecs »
  étaient en réalité de bons refus : « la BCM *ne fournit pas* de taux pour
  2027 », « le cours au 31 juillet 2026 *est absent* ». Ils refusent le fait
  demandé **tout en citant le contexte disponible** — plus utile qu'un « je ne
  trouve pas » sec. La métrique assimilait à tort « refus » et « aucune
  citation ».
- **Page attendue citée** affichait 0 % sur les Lettres : l'extraction ne
  reconnaissait que `[p. PDF N]` et ignorait `[Lettre d'information Mars 2026,
  p. 2]`. Valeur réelle : 87,1 %.

Leçon d'exploitation : une métrique inattendue se vérifie avant de conclure à
une panne. Les deux auraient conduit à « corriger » un comportement correct.

## Ce qui reste imparfait

**Rapport annuel — 2 cas sur 29**

| Cas | Type | Manque |
|---|---|---|
| `money_02` | paraphrase | la proportion « 68 % » |
| `foreign_deposits_01` | raisonnement | le montant « 3 224 MMRU » |

**Lettres d'information — 7 cas sur 70**

| Cas | Type | Manque |
|---|---|---|
| `li2605_04` | direct | pic d'inflation « 10,2 % », échéance |
| `li2607_05` | direct | « 300 millions de dollars », « juin 2026 » |
| `li2607_13` | direct | « règle de Taylor » |
| `li2606_08` | liste | 2 axes sur 4 |
| `licross_01` | raisonnement | une étape du cheminement du taux |
| `licross_03` | raisonnement | « 2,4 milliards » |
| `licross_06` | liste | le programme « HIMAYA » |

Le motif est net : les réponses **partielles** dominent, sur des questions de
liste ou de raisonnement enchaînant plusieurs sources. La réponse est juste mais
un chiffre ou un élément manque. Les échecs francs restent rares.

Piste principale : élargir la tranche de preuves transmise pour ces deux types
de questions, comme le fait déjà `broad_question`. À mesurer avec ce harnais
avant d'appliquer quoi que ce soit.
