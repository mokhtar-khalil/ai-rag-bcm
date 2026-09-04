"""Exporte les KPI du pilote depuis SQLite local ou PostgreSQL Railway.

Exemples :
    python scripts/analytics_report.py --days 30
    python scripts/analytics_report.py --days 60 --format json --output rapport.json
    python scripts/analytics_report.py --include-content --output bilan_pilote.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.analytics import analytics_snapshot, content_gap_examples  # noqa: E402
from core.config import get_settings  # noqa: E402


LABELS = {
    "consented_sessions": "Sessions consenties",
    "interactions": "Interactions",
    "questions_per_session": "Questions par session",
    "feedback_count": "Retours reçus",
    "feedback_coverage_pct": "Couverture du feedback (%)",
    "satisfaction_pct": "Satisfaction (%)",
    "resolution_pct": "Besoins résolus (%)",
    "grounded_pct": "Réponses sourcées (%)",
    "clarification_pct": "Clarifications (%)",
    "fallback_pct": "Replis après erreur modèle (%)",
    "latency_p50_ms": "Latence p50 (ms)",
    "latency_p95_ms": "Latence p95 (ms)",
    "total_tokens": "Tokens totaux",
    "token_measurement_coverage_pct": "Couverture de mesure des tokens (%)",
    "tokens_per_interaction": "Tokens par interaction",
}


def _value(value: Any) -> str:
    """Affiche clairement une métrique absente au lieu de la transformer en zéro."""
    return "Non mesuré" if value is None else str(value)


def _section(title: str, values: dict[str, Any]) -> list[str]:
    lines = [f"## {title}", "", "| Indicateur | Valeur |", "|---|---:|"]
    lines.extend(f"| {key} | {_value(value)} |" for key, value in values.items())
    lines.append("")
    return lines


def markdown_report(snapshot: dict[str, Any], gaps: list[dict[str, str]]) -> str:
    """Transforme les agrégats en rapport lisible et transmissible au client."""
    lines = [
        "# Bilan analytique — Assistant des publications de la BCM",
        "",
        f"Période analysée : {snapshot['period_days']} derniers jours.  ",
        f"Généré le : {snapshot['generated_at']}.",
        "",
        "> Les sessions comptées sont uniquement celles ayant accepté la mesure d’audience. ",
        "> Elles ne représentent donc pas nécessairement tous les visiteurs du site.",
        "",
    ]
    overview = {
        LABELS.get(key, key): value for key, value in snapshot["overview"].items()
    }
    lines.extend(_section("Synthèse exécutive", overview))
    lines.extend(["## Insights actionnables", ""])
    lines.extend(
        f"- {insight}" for insight in snapshot.get("insights", [])
    )
    if not snapshot.get("insights"):
        lines.append("- Aucun signal prioritaire sur la période.")
    lines.append("")
    lines.extend(_section("Consommation de tokens", snapshot["tokens"]))
    lines.extend(
        [
            "## Qualité par langue",
            "",
            "| Langue | Interactions | Feedback | Satisfaction (%) | Sourcées (%) | Latence p50 (ms) |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for language, values in snapshot.get("quality_by_language", {}).items():
        lines.append(
            f"| {language} | {values['interactions']} | {values['feedback']} | "
            f"{_value(values['satisfaction_pct'])} | {_value(values['grounded_pct'])} | "
            f"{_value(values['latency_p50_ms'])} |"
        )
    if not snapshot.get("quality_by_language"):
        lines.append("| Aucune donnée | 0 | 0 | Non mesuré | Non mesuré | Non mesuré |")
    lines.append("")
    for key, title in (
        ("by_day", "Évolution quotidienne"),
        ("by_language", "Utilisation par langue"),
        ("by_topic", "Sujets recherchés"),
        ("by_status", "Résultats du pipeline"),
        ("by_provider_model", "Fournisseurs et modèles"),
        ("feedback_reasons", "Motifs des retours"),
        ("ui_events", "Parcours dans l’interface"),
        ("top_sources", "Sources les plus mobilisées"),
    ):
        lines.extend(_section(title, snapshot[key] or {"Aucune donnée": 0}))
    if gaps:
        lines.extend(
            [
                "## Questions à examiner",
                "",
                "> Cette section contient du texte consenti. Ne la diffusez qu’aux personnes autorisées.",
                "",
                "| Sujet | Statut | Motif | Question |",
                "|---|---|---|---|",
            ]
        )
        for item in gaps:
            question = item["question"].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {item['topic']} | {item['status']} | "
                f"{item['feedback_reason'] or '—'} | {question} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Lecture recommandée",
            "",
            "- Ne concluez pas sur un taux sans afficher son volume d’observations.",
            "- Analysez séparément le français et l’arabe, ainsi que chaque fournisseur de génération.",
            "- Priorisez les sujets cumulant refus, clarifications et retours négatifs.",
            "- Le coût monétaire doit être calculé dans l’outil central avec la grille tarifaire effective du modèle à la date de l’appel.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="Fenêtre d'analyse (défaut : 30).")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path, help="Fichier cible ; sinon sortie standard.")
    parser.add_argument(
        "--include-content",
        action="store_true",
        help="Inclut les questions consenties problématiques dans l'export.",
    )
    parser.add_argument("--gap-limit", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.days <= 3660:
        parser.error("--days doit être compris entre 1 et 3660")

    settings = get_settings()
    snapshot = analytics_snapshot(settings, args.days)
    gaps = (
        content_gap_examples(settings, args.days, args.gap_limit)
        if args.include_content
        else []
    )
    if args.format == "json":
        payload: dict[str, Any] = dict(snapshot)
        if args.include_content:
            payload["content_gaps"] = gaps
        content = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        content = markdown_report(snapshot, gaps)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")
        print(f"Rapport écrit : {args.output}")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
