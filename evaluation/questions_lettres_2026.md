# Jeu de test RAG — Lettres d'information de la BCM, éditions 2026

76 questions adossées aux sept éditions de [data/lettres_information/](../data/lettres_information).
Version exploitable par un script : `questions_lettres_2026.jsonl`.

`answer_contains` liste les éléments qu'une bonne réponse doit citer ; `expected_pages`
donne les pages du PDF où l'information se trouve réellement, ce qui permet de noter le
retrieval indépendamment de la génération.

## Janvier 2026 (N° 05, 1 p.)

| id | Question | Pages | Attendu dans la réponse | Type |
|---|---|---|---|---|
| `li2601_01` | Quand s'est tenue la réunion mensuelle de concertation entre la BCM et les banques primaires en janvier 2026 ? | 1 | 28 janvier 2026 | direct |
| `li2601_02` | Quelle charte a été signée lors de la réunion de janvier 2026 avec les dirigeants des banques primaires ? | 1 | Charte Interbancaire de Conformité | direct |
| `li2601_03` | À quelle date le Conseil des Systèmes de Paiement, de Compensation et de Règlement des Titres s'est-il réuni en janvier 2026 et où ? | 1 | 29 janvier 2026 · Nouakchott | direct |
| `li2601_04` | Quelles infrastructures du Système National de Paiement le CSPCT a-t-il passées en revue ? | 1 | RTGS · ACH · CSD · ATS | liste |
| `li2601_05` | Avec quelle agence des Nations Unies la BCM a-t-elle signé un mémorandum d'entente le 27 janvier 2026, et sur quel sujet ? | 1 | HCR · réfugiés | direct |
| `li2601_06` | Quels publics le partenariat entre la BCM et le Programme Alimentaire Mondial vise-t-il en priorité ? | 1 | femmes · jeunes · populations vulnérables | paraphrase |
| `li2601_07` | Quelle étude de faisabilité en matière de messagerie financière a été évoquée avec les banques primaires en janvier 2026 ? | 1 | Service Bureau SWIFT | raisonnement |

## Février 2026 (N° 06, 1 p.)

| id | Question | Pages | Attendu dans la réponse | Type |
|---|---|---|---|---|
| `li2602_01` | Sur quelle période s'est déroulée la mission FSSR du FMI en février 2026 ? | 1 | 3 au 16 février 2026 | direct |
| `li2602_02` | Dans la continuité de quels travaux la mission FSSR de février 2026 s'inscrivait-elle ? | 1 | septembre 2025 | raisonnement |
| `li2602_03` | Quelle convention scientifique le CEEM a-t-il signée le 12 février 2026 et sur quel projet porte-t-elle ? | 1 | Mission Archéologique Azougui-Teyart · MAAT | direct |
| `li2602_04` | Quel comité chargé du pilotage stratégique du système d'information a tenu sa première réunion en février 2026 ? | 1 | Comité de Gouvernance IT · COGIT | direct |
| `li2602_05` | Quel projet la mission conjointe Banque mondiale–SFI préparait-elle et à quelles entreprises est-il destiné ? | 1 | PforR · MPME | paraphrase |
| `li2602_06` | Qui conduisait la délégation de la Commission économique des Nations Unies pour l'Afrique reçue par la BCM ? | 1 | Claver Gatete | direct |
| `li2602_07` | À l'occasion de quel anniversaire national des membres du personnel de la BCM ont-ils été décorés ? | 1 | 65 | direct |

## Mars 2026 (N° 07, 6 p.)

| id | Question | Pages | Attendu dans la réponse | Type |
|---|---|---|---|---|
| `li2603_01` | Quel montant de financements l'accord-cadre signé avec l'ICD prévoit-il de mobiliser, et sur quelle période ? | 1 | 900 millions de dollars · 2026-2028 | direct |
| `li2603_02` | Où a été signé l'accord entre la Mauritanie et la Société internationale islamique pour le financement du secteur privé ? | 1 | Djeddah | paraphrase |
| `li2603_03` | Combien de plateformes numériques structurent le dispositif de supervision de la BCM présenté en mars 2026 ? | 3, 6 | six · 6 | direct |
| `li2603_04` | Combien de cautions bancaires ont été émises par les banques sur la plateforme d'authentification, et pour quel montant cumulé ? | 3 | 13 055 · 56,9 | direct |
| `li2603_05` | Quand la Banking Supervision Application (BSA) a-t-elle été installée ? | 4 | juin 2024 | direct |
| `li2603_06` | À quelle date le Système d'Information sur le Crédit est-il entré en vigueur ? | 5 | 1 avril 2025 | direct |
| `li2603_07` | Combien d'étudiants de Master 2 la Chaire d'Économie Monétaire a-t-elle soutenus pour une mobilité Erasmus+, et vers quelle université ? | 2 | dix · Palerme | direct |
| `li2603_08` | Sur quelle période s'est déroulée la mission d'assistance technique du FMI consacrée au FPAS ? | 2 | 23 mars · 3 avril 2026 | direct |
| `li2603_09` | Quel outil assiste les inspecteurs dans l'analyse de la classification des créances lors des contrôles sur place ? | 5 | TASNIF | paraphrase |

## Avril 2026 (N° 08, 6 p.)

| id | Question | Pages | Attendu dans la réponse | Type |
|---|---|---|---|---|
| `li2604_01` | Quel a été le taux de participation à l'élection des délégués du personnel de la BCM en avril 2026 ? | 1 | 68,28 % | direct |
| `li2604_02` | Quand s'est tenue la première réunion du Conseil d'Administration de la Bourse de Nouakchott SA ? | 1 | 6 avril 2026 | direct |
| `li2604_03` | Sur quelle période se sont tenues les Réunions de printemps du FMI et de la Banque mondiale auxquelles le Gouverneur a participé ? | 4 | 13 au 19 avril 2026 · Washington | direct |
| `li2604_04` | Quels guichets du FMI le programme économique 2023-2026 de la Mauritanie mobilise-t-il ? | 4 | FEC · MEDC · FRD | liste |
| `li2604_05` | Autour de quels axes s'articule le nouveau programme en cours de finalisation avec le FMI ? | 4 | stabilité macroéconomique · croissance inclusive · gouvernance | liste |
| `li2604_06` | Quand est paru le premier numéro des Cahiers d'Études en Économie Monétaire, et quelle est sa particularité dans l'histoire de la BCM ? | 3 | avril 2026 · première revue | raisonnement |
| `li2604_07` | Qui a animé la conférence du cycle « L'Invité du CEEM » consacrée au risque climatique en avril 2026 ? | 2 | Yacoub Bahini | direct |
| `li2604_08` | Quel réseau dédié aux femmes dans le secteur financier la BCM a-t-elle réuni avec la SFI en avril 2026 ? | 2 | Women in Finance Mauritanie | direct |

## Mai 2026 (N° 09, 8 p.)

| id | Question | Pages | Attendu dans la réponse | Type |
|---|---|---|---|---|
| `li2605_01` | De combien le Conseil de Politique Monétaire a-t-il relevé le taux directeur le 18 mai 2026, et à quel niveau ? | 1 | 50 points de base · 6,00 % · 6,50 % | direct |
| `li2605_02` | Quel était le nouveau niveau du taux directeur de la BCM après la réunion de mai 2026 ? | 1 | 6,50 % | paraphrase |
| `li2605_03` | Comment l'inflation a-t-elle évolué entre mars et avril 2026 selon le Conseil de Politique Monétaire ? | 1 | 4,7 % · 7,6 % | direct |
| `li2605_04` | À quel niveau la BCM estimait-elle le pic d'inflation, et à quelle échéance ? | 1 | 10,2 % · septembre 2026 | direct |
| `li2605_05` | Quel était le niveau des réserves extérieures brutes et de la liquidité bancaire au moment de la décision de mai 2026 ? | 1 | 2,4 milliards · 60 milliards MRU | liste |
| `li2605_06` | Combien d'objectifs guidaient la décision de relèvement du taux directeur de mai 2026 ? | 1 | cinq | raisonnement |
| `li2605_07` | Quel était le thème de la 28e Conférence des Gouverneurs des banques centrales francophones, et où s'est-elle tenue ? | 4, 5 | autonomie des banques centrales · Phnom Penh | direct |
| `li2605_08` | Combien de domaines de coopération couvre l'Accord signé avec la Banque de France en mai 2026 ? | 5 | sept | direct |
| `li2605_09` | Quel événement international la BCM accueillera-t-elle à Nouakchott, et à quelles dates ? | 5 | 21 au 23 septembre 2026 · Nouakchott | direct |
| `li2605_10` | À quel niveau l'inflation avait-elle été ramenée fin 2023, selon l'intervention du Gouverneur à Phnom Penh ? | 4 | sous les 3 % · fin 2023 | raisonnement |

## Juin 2026 (N° 10, 8 p.)

| id | Question | Pages | Attendu dans la réponse | Type |
|---|---|---|---|---|
| `li2606_01` | Quel montant d'approbations de financement le Groupe de la BID a-t-il enregistré en 2025, et avec quelle progression ? | 1 | 16 milliards de dollars · 20 | direct |
| `li2606_02` | Quel nouveau cadre stratégique le Président du Groupe de la BID a-t-il présenté à Bakou ? | 2 | 2026-2035 | direct |
| `li2606_03` | À quelles dates et où s'est tenue la 96e Assemblée générale annuelle de la Banque des Règlements Internationaux ? | 3 | 27 et 28 juin 2026 · Bâle | direct |
| `li2606_04` | Qui a prononcé la Per Jacobsson Lecture lors de l'Assemblée de la BRI, et sur quel sujet ? | 4 | Gita Gopinath · stablecoins | direct |
| `li2606_05` | Par quel numéro court la plateforme HIMAYA est-elle joignable, et dans quelles langues accueille-t-elle les usagers ? | 5 | 1973 · pulaar · soninké · wolof · hassaniya | liste |
| `li2606_06` | Quels résultats HIMAYA affiche-t-elle après trois mois d'activité ? | 5 | 50 · 400 · 95 | direct |
| `li2606_07` | Quels programmes de la Banque africaine de développement la délégation de la BAD a-t-elle salués en juin 2026 ? | 3 | PAMIF I · PAMIF II | direct |
| `li2606_08` | Quels sont les quatre axes prioritaires retenus pour la coopération entre la BCM et la Banque de France ? | 7 | circuit fiduciaire · monnaie numérique · prévision économique · stabilité financière | liste |
| `li2606_09` | Quels chantiers du secteur financier ont été passés en revue lors de la visite à la banque d'affaires Lazard ? | 8 | consolidation du secteur bancaire · Taux Effectif Global · résolution bancaire · créances non performantes | liste |
| `li2606_10` | Quel geste institutionnel le nouveau Gouverneur de la Banque de France a-t-il eu envers la délégation mauritanienne ? | 1, 6 | Emmanuel Moulin · deuxième jour | raisonnement |

## Juillet 2026 (N° 11, 13 p.)

| id | Question | Pages | Attendu dans la réponse | Type |
|---|---|---|---|---|
| `li2607_01` | Quelle décision le Conseil de Politique Monétaire a-t-il prise le 14 juillet 2026 ? | 1 | 25 points de base · 6,50 % · 6,75 % | direct |
| `li2607_02` | À quel niveau le taux de la facilité de prêt a-t-il été porté en juillet 2026 ? | 1, 9 | 7,00 % | direct |
| `li2607_03` | Quel était le niveau des avoirs extérieurs bruts de la BCM à fin juin 2026, et sa progression par rapport à fin 2025 ? | 2 | 2,65 milliards · 21,8 | direct |
| `li2607_04` | Quels revenus les placements des réserves ont-ils générés au premier semestre 2026 ? | 2 | 40,15 millions | direct |
| `li2607_05` | À quel montant la BCM a-t-elle porté les réserves confiées au mandat de gestion de la Banque mondiale, et quand ? | 5 | 300 millions de dollars · juin 2026 | direct |
| `li2607_06` | Quand la BCM a-t-elle publié son Rapport annuel au titre de l'exercice 2025 ? | 3 | 31 juillet 2026 | direct |
| `li2607_07` | Quelle institution de supervision régionale la BCM a-t-elle accueillie en juillet 2026, et pour quel motif ? | 4 | COBAC · reporting prudentiel numérisé | direct |
| `li2607_08` | Comment la largeur du corridor des taux d'intérêt a-t-elle évolué entre 2022 et 2025, et où s'établit-elle après juillet 2026 ? | 9 | 900 points de base · 450 points de base · 500 points de base | raisonnement |
| `li2607_09` | Comment la liquidité bancaire a-t-elle évolué entre fin 2022 et 2026 ? | 10 | 30 milliards · 70 milliards | raisonnement |
| `li2607_10` | Quels modèles la BCM croise-t-elle pour prévoir l'inflation, et à quels horizons ? | 11 | ARMA · VECM · un à trois mois · douze à vingt-quatre mois | liste |
| `li2607_11` | Quelle plateforme scientifique diffuse désormais les Cahiers d'Études en Économie Monétaire ? | 1, 7 | Cairn.info | direct |
| `li2607_12` | Sur quelle période s'est déroulée la mission d'évaluation des sauvegardes du FMI, et quand la précédente avait-elle eu lieu ? | 5 | 2 au 12 juillet 2026 · 2023 | raisonnement |
| `li2607_13` | Quelle règle de politique monétaire figure dans le bloc monétaire du Modèle de Projection Trimestriel ? | 12 | règle de Taylor | direct |

## Questions transverses (plusieurs éditions)

Elles ne sont satisfaites que si le retrieval va chercher dans plusieurs lettres à la fois.

| id | Question | Sources | Attendu dans la réponse |
|---|---|---|---|
| `licross_01` | Quel a été le cheminement du taux directeur de la BCM entre mai et juillet 2026 ? | Mai 2026 p. 1 · Juillet 2026 p. 1, 8 | 6,00 % · 6,50 % · 6,75 % |
| `licross_02` | Combien de fois le Conseil de Politique Monétaire s'est-il réuni en 2026 selon les lettres d'information, et à quelles dates ? | Mai 2026 p. 1 · Juillet 2026 p. 1, 4 | 18 mai 2026 · 14 juillet 2026 |
| `licross_03` | Comment les réserves extérieures de la Mauritanie ont-elles évolué entre la lettre de mai et celle de juillet 2026 ? | Mai 2026 p. 1 · Juillet 2026 p. 2 | 2,4 milliards · 2,65 milliards |
| `licross_04` | Quelles étapes ont jalonné la coopération entre la BCM et la Banque de France en 2026 ? | Mai 2026 p. 5 · Juin 2026 p. 6 · Juillet 2026 p. 1, 6 | Phnom Penh · Paris · Clermont-Ferrand |
| `licross_05` | Quel parcours les Cahiers d'Études en Économie Monétaire ont-ils suivi depuis leur lancement ? | Avril 2026 p. 3 · Mai 2026 p. 6 · Juillet 2026 p. 1, 7 | avril 2026 · cinq articles · Cairn.info |
| `licross_06` | Quels engagements la BCM a-t-elle pris en faveur de l'inclusion financière des populations vulnérables en 2026 ? | Janvier 2026 p. 1 · Avril 2026 p. 2 · Juin 2026 p. 5 | HCR · PAM · Women in Finance · HIMAYA |

## Questions sans réponse (test du refus)

Plausibles dans le contexte, mais absentes des lettres. La bonne réponse est de le dire,
pas d'inventer un chiffre ni de le tirer du Rapport annuel.

| id | Question |
|---|---|
| `liabs_01` | Quel taux de croissance du PIB la BCM prévoit-elle pour la Mauritanie en 2027 ? |
| `liabs_02` | Quel est le montant des réserves obligatoires imposé aux banques en août 2026 ? |
| `liabs_03` | Quelle décision le Conseil de Politique Monétaire a-t-il prise lors de sa réunion de septembre 2026 ? |
| `liabs_04` | Combien de banques sont agréées en Mauritanie fin 2026 ? |
| `liabs_05` | Quel est le cours du dollar face à l'ouguiya au 31 juillet 2026 ? |
| `liabs_06` | Quelle est la cible d'inflation chiffrée officiellement annoncée par la BCM ? |
