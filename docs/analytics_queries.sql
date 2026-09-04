-- Requêtes PostgreSQL/SQLite simples pour l'analyse du pilote BCM.
-- Les taux complets et percentiles portables sont produits par
-- scripts/analytics_report.py.

-- Volume par jour et par langue.
SELECT substr(CAST(created_at AS TEXT), 1, 10) AS jour,
       language,
       count(*) AS interactions
FROM logged_questions
GROUP BY 1, 2
ORDER BY 1, 2;

-- Thèmes et résultats les plus fréquents.
SELECT topic, status, count(*) AS total
FROM logged_questions
GROUP BY topic, status
ORDER BY total DESC;

-- Consommation par fournisseur, modèle et opération.
SELECT provider,
       model,
       operation,
       count(*) AS appels,
       sum(input_tokens) AS tokens_entree,
       sum(cached_input_tokens) AS tokens_entree_cache,
       sum(output_tokens) AS tokens_sortie,
       sum(reasoning_tokens) AS tokens_raisonnement,
       sum(total_tokens) AS tokens_total,
       avg(latency_ms) AS latence_moyenne_ms
FROM model_calls
GROUP BY provider, model, operation
ORDER BY tokens_total DESC;

-- Satisfaction et résolution par langue.
SELECT language,
       count(*) AS retours,
       sum(CASE WHEN rating = 'up' THEN 1 ELSE 0 END) AS positifs,
       sum(CASE WHEN resolved THEN 1 ELSE 0 END) AS resolus
FROM answer_feedback
GROUP BY language;

-- Motifs à corriger en priorité.
SELECT reason, count(*) AS total
FROM answer_feedback
WHERE rating = 'down'
GROUP BY reason
ORDER BY total DESC;

-- Parcours dans l'interface.
SELECT event_type, count(*) AS total, count(DISTINCT session_id) AS sessions
FROM ui_events
GROUP BY event_type
ORDER BY total DESC;
