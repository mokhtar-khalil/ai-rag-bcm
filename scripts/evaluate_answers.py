"""Évalue la chaîne complète — récupération, sélection, génération — sur un jeu de test.

`evaluate_retrieval.py` ne mesure que la récupération : il dit si la bonne page
est retrouvée, jamais si la réponse est juste. Or les défauts constatés en usage
portaient sur les réponses, pas sur les pages. Ce harnais interroge donc
`POST /api/ask` et vérifie ce que l'utilisateur lit réellement.

Usage :
    python scripts/evaluate_answers.py --dataset evaluation/questions.jsonl
    python scripts/evaluate_answers.py --dataset evaluation/questions_lettres_2026.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api.app import create_app  # noqa: E402
from core.config import get_settings  # noqa: E402


def _normalise(valeur: str) -> str:
    """Rapproche deux écritures d'un même fait : espaces, casse, signes."""
    texte = unicodedata.normalize("NFKC", valeur).casefold()
    # Espaces insécables des milliers, isolats bidi arabes, ponctuation fine.
    texte = re.sub(r"[  ⁦-⁩‎‏]", " ", texte)
    texte = texte.replace("’", "'").replace("٪", "%").replace("%", " % ")
    return re.sub(r"\s+", " ", texte).strip()


def _fait_present(fait: str, reponse: str) -> bool:
    """Vérifie qu'un fait attendu figure dans la réponse, à l'écriture près."""
    attendu = _normalise(fait)
    obtenu = _normalise(reponse)
    if attendu in obtenu:
        return True
    # « 4,0% » et « 4,0 % » désignent la même valeur ; idem pour « 4.0 % ».
    souple = attendu.replace(" ", "").replace(".", ",")
    return souple in obtenu.replace(" ", "").replace(".", ",")


# Un refus n'est pas une réponse vide : les meilleurs refusent le fait demandé
# tout en citant le contexte disponible. Les reconnaître par ces marqueurs évite
# de compter comme un échec une réponse plus utile que « je ne trouve pas ».
MARQUEURS_REFUS = (
    "ne trouve pas", "est absente", "sont absentes", "n'apparait", "n'apparaît",
    "ne figure pas", "ne figurent pas", "ne fournit pas", "ne fournissent pas",
    "ne donnent pas", "ne donne pas", "ne precise pas", "ne précise pas",
    "aucune cible", "aucun taux", "aucune information", "aucune donnee",
    "aucune donnée", "ne permettent pas", "ne permet pas", "sans valeur precisee",
    # Équivalents arabes : « je ne trouve pas », « absent », « ne comportent pas ».
    "لا أجد", "لا تتضمن", "غير متوفر", "لا توجد", "لا يمكن", "لم ترد", "غير مذكور",
)


def _est_un_refus(reponse: str) -> bool:
    """Reconnaît une réponse qui décline le fait demandé, citations comprises."""
    texte = _normalise(reponse)
    return any(_normalise(marqueur) in texte for marqueur in MARQUEURS_REFUS)


def evaluer(cas: list[dict], client, verbeux: bool = False) -> list[dict]:
    """Interroge l'API pour chaque cas et confronte la réponse aux attentes."""
    resultats = []
    for index, item in enumerate(cas, start=1):
        debut = time.perf_counter()
        langue = item.get("language") or (
            "ar" if re.search(r"[\u0600-\u06FF]", item["question"]) else "fr"
        )
        reponse = client.post(
            "/api/ask", json={"question": item["question"], "language": langue}
        ).json
        texte = reponse.get("answer", "")
        pages_citees = {
            int(page) for page in re.findall(r"\[p\. PDF (\d+)\]", texte)
        }
        # Une Lettre d'information se cite par son mois et sa page interne :
        # ne chercher que « p. PDF N » donnait 0 % de pages correctes sur un
        # jeu où toutes les citations étaient pourtant justes.
        pages_citees |= {
            int(page)
            for page in re.findall(
                r"\[Lettre d[’']information [^\],]{1,40}, p\.\s*(\d+)\]", texte
            )
        }
        attendues = set(item.get("expected_pages") or [])
        faits = item.get("answer_contains") or []
        trouves = [fait for fait in faits if _fait_present(fait, texte)]
        absent = item.get("difficulty") == "absent"

        resultats.append(
            {
                "id": item["id"],
                "question": item["question"],
                "difficulty": item.get("difficulty", "?"),
                "category": item.get("category", "?"),
                "grounded": bool(reponse.get("grounded")),
                "clarification": bool(reponse.get("clarification_needed")),
                "attendu_absent": absent,
                "refuse": _est_un_refus(texte),
                "faits_attendus": len(faits),
                "faits_trouves": len(trouves),
                "faits_manquants": [f for f in faits if f not in trouves],
                # Une citation hors des pages attendues n'est pas forcément
                # fausse : le fait peut figurer ailleurs. On la signale sans
                # la compter comme une erreur.
                "page_attendue_citee": bool(pages_citees & attendues) if attendues else None,
                "pages_citees": sorted(pages_citees),
                "sources": [s.get("citation") for s in reponse.get("sources", [])][:3],
                "duree": round(time.perf_counter() - debut, 2),
                "answer": texte,
            }
        )
        if verbeux:
            r = resultats[-1]
            etat = "OK " if (r["faits_trouves"] == r["faits_attendus"]) else "MANQUE"
            print(f"  [{index:3d}/{len(cas)}] {etat} {item['id']:14s} "
                  f"{r['faits_trouves']}/{r['faits_attendus']} faits  {r['duree']:5.1f}s",
                  flush=True)
    return resultats


def synthese(resultats: list[dict]) -> dict:
    """Agrège les résultats par difficulté et calcule les indicateurs clés."""
    # Un cas sans fait attendu ne peut ni réussir ni échouer sur ce critère :
    # le compter partout donnait 94 % de réponses complètes et 23 % de réponses
    # sans aucun fait, deux chiffres inconciliables.
    repondables = [
        r for r in resultats if not r["attendu_absent"] and r["faits_attendus"] > 0
    ]
    absents = [r for r in resultats if r["attendu_absent"]]

    def taux(sous_ensemble, predicat):
        if not sous_ensemble:
            return None
        return round(sum(1 for r in sous_ensemble if predicat(r)) / len(sous_ensemble), 4)

    par_difficulte = {}
    for r in repondables:
        d = r["difficulty"]
        par_difficulte.setdefault(d, []).append(r)

    return {
        "cas": len(resultats),
        "repondables": len(repondables),
        "hors_corpus": len(absents),
        "faits_complets": taux(repondables, lambda r: r["faits_trouves"] == r["faits_attendus"]),
        "faits_partiels": taux(repondables, lambda r: 0 < r["faits_trouves"] < r["faits_attendus"]),
        "faits_absents": taux(repondables, lambda r: r["faits_trouves"] == 0),
        "fondees": taux(repondables, lambda r: r["grounded"]),
        "clarifications_parasites": taux(repondables, lambda r: r["clarification"]),
        # Un refus reste correct même s'il cite le contexte qui l'explique.
        "refus_corrects": taux(absents, lambda r: r["refuse"] or not r["grounded"]),
        "page_attendue_citee": taux(
            [r for r in repondables if r["page_attendue_citee"] is not None],
            lambda r: r["page_attendue_citee"],
        ),
        "duree_moyenne": round(
            sum(r["duree"] for r in resultats) / max(len(resultats), 1), 2
        ),
        "par_difficulte": {
            d: {
                "n": len(v),
                "faits_complets": taux(v, lambda r: r["faits_trouves"] == r["faits_attendus"]),
                "clarifications": taux(v, lambda r: r["clarification"]),
            }
            for d, v in sorted(par_difficulte.items())
        },
    }


def main() -> int:
    """Exécute l'évaluation demandée et écrit le rapport détaillé."""
    parser = argparse.ArgumentParser(description="Évalue les réponses de bout en bout.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cas = [json.loads(ligne) for ligne in args.dataset.read_text(encoding="utf-8").splitlines() if ligne.strip()]
    if args.limit:
        cas = cas[: args.limit]

    settings = get_settings()
    client = create_app(settings_override=settings).test_client()
    print(f"{len(cas)} cas — fournisseur {settings.generation_provider}", flush=True)
    resultats = evaluer(cas, client, verbeux=not args.quiet)
    rapport = {"dataset": str(args.dataset), "metriques": synthese(resultats), "cas": resultats}
    print(json.dumps(rapport["metriques"], ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Rapport détaillé : {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
