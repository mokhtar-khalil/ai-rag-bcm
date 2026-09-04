# Bilan analytique — Assistant des publications de la BCM

Période analysée : 30 derniers jours.  
Généré le : 2026-09-03T11:43:05.185169+00:00.

> Les sessions comptées sont uniquement celles ayant accepté la mesure d’audience. 
> Elles ne représentent donc pas nécessairement tous les visiteurs du site.

## Synthèse exécutive

| Indicateur | Valeur |
|---|---:|
| Sessions consenties | 6 |
| Interactions | 7 |
| Questions par session | 1.17 |
| Retours reçus | 7 |
| Couverture du feedback (%) | 50.0 |
| Satisfaction (%) | 71.4 |
| Besoins résolus (%) | 66.7 |
| Réponses sourcées (%) | 100.0 |
| Clarifications (%) | 0.0 |
| Replis après erreur modèle (%) | 0.0 |
| Latence p50 (ms) | 10031.6 |
| Latence p95 (ms) | 21214.5 |
| Tokens totaux | 47239 |
| Couverture de mesure des tokens (%) | 85.7 |
| Tokens par interaction | 7873.2 |

## Insights actionnables

- Échantillon insuffisant pour une conclusion client : viser au moins 30 interactions consenties avant d'interpréter les taux.
- La satisfaction est inférieure à 75 % : examiner d'abord les motifs incorrect, incomplet et source manquante.
- La latence p95 dépasse 10 secondes : ventiler model_calls entre planification, reranking et génération pour cibler l'étape lente.
- Le thème dominant est « conjoncture_inflation » (5 interaction(s)) : prioriser sa couverture documentaire et ses tests métier.

## Consommation de tokens

| Indicateur | Valeur |
|---|---:|
| input | 45292 |
| cached_input | 5172 |
| output | 1947 |
| reasoning | 281 |
| total | 47239 |

## Qualité par langue

| Langue | Interactions | Feedback | Satisfaction (%) | Sourcées (%) | Latence p50 (ms) |
|---|---:|---:|---:|---:|---:|
| ar | 1 | 1 | 100.0 | 100.0 | 2458.2 |
| fr | 6 | 2 | 50.0 | 100.0 | 10132.0 |

## Évolution quotidienne

| Indicateur | Valeur |
|---|---:|
| 2026-08-31 | 1 |
| 2026-09-03 | 6 |

## Utilisation par langue

| Indicateur | Valeur |
|---|---:|
| fr | 6 |
| ar | 1 |

## Sujets recherchés

| Indicateur | Valeur |
|---|---:|
| conjoncture_inflation | 5 |
| non_classe | 1 |
| systemes_paiement | 1 |

## Résultats du pipeline

| Indicateur | Valeur |
|---|---:|
| answered | 6 |
| legacy | 1 |

## Fournisseurs et modèles

| Indicateur | Valeur |
|---|---:|
| openai / gpt-5.6-terra | 6 |
| inconnu / inconnu | 1 |

## Motifs des retours

| Indicateur | Valeur |
|---|---:|
| sans_raison | 4 |
| helpful | 2 |
| incomplete | 1 |

## Parcours dans l’interface

| Indicateur | Valeur |
|---|---:|
| question_submitted | 6 |
| widget_opened | 5 |
| consent_accepted | 5 |
| response_received | 2 |
| sources_opened | 1 |

## Sources les plus mobilisées

| Indicateur | Valeur |
|---|---:|
| p. PDF 5 | 5 |
| p. PDF 21 | 2 |
| p. PDF 23 | 2 |
| p. PDF 122 | 1 |
| p. PDF 25 | 1 |
| p. PDF 64 | 1 |
| p. PDF 65 | 1 |
| Lettre d'information Mai 2026, p. 3 | 1 |

## Lecture recommandée

- Ne concluez pas sur un taux sans afficher son volume d’observations.
- Analysez séparément le français et l’arabe, ainsi que chaque fournisseur de génération.
- Priorisez les sujets cumulant refus, clarifications et retours négatifs.
- Le coût monétaire doit être calculé dans l’outil central avec la grille tarifaire effective du modèle à la date de l’appel.

