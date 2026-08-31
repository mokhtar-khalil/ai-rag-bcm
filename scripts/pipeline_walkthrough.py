"""Rejoue tout le pipeline RAG BCM de zéro, étape par étape, avec explications.

Pensé comme un script d'entraînement de modèle : chaque étape est un "epoch"
numéroté, chronométré, et commenté pour comprendre POURQUOI le pipeline fait
ce qu'il fait — pas seulement QUOI. Aucune étape ne nécessite de clé API par
défaut (le mode de génération par défaut est "extractive").

Usage :
    python scripts/pipeline_walkthrough.py                  # run complet, cache réutilisé
    python scripts/pipeline_walkthrough.py --force            # reconstruit tout depuis zéro
    python scripts/pipeline_walkthrough.py --skip-semantic     # démo lexicale uniquement
    python scripts/pipeline_walkthrough.py --skip-eval         # saute la mesure de qualité
    python scripts/pipeline_walkthrough.py --ask "..." --provider openai --language fr
"""

from __future__ import annotations

import argparse
import sys
import textwrap
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from api.embeddings import embed_query, warm_embedding_model  # noqa: E402
from api.providers import answer_with_provider, resolve_provider  # noqa: E402
from api.query import build_retrieval_query  # noqa: E402
from api.rag import RAGIndex  # noqa: E402
from api.sources import load_corpus  # noqa: E402
from core.config import get_settings  # noqa: E402

import evaluate_retrieval  # noqa: E402  (scripts/evaluate_retrieval.py)


TOTAL_STEPS = 8


# ──────────────────────────────────────────────────────────────────────────
# Affichage : reproduit le style d'un journal d'entraînement (bannières par
# étape, explications en retrait, chronométrage par étape, résumé final).
# ──────────────────────────────────────────────────────────────────────────


class Reporter:
    """Imprime la progression du pipeline dans un format lisible et pédagogique."""

    def __init__(self, use_color: bool) -> None:
        self.use_color = use_color
        self.stage_times: list[tuple[str, float]] = []
        self._run_started = time.perf_counter()

    def _c(self, code: str) -> str:
        return code if self.use_color else ""

    def banner(self, step: int, title: str) -> None:
        bold, cyan, reset = self._c("\033[1m"), self._c("\033[36m"), self._c("\033[0m")
        rule = "─" * 78
        print(f"\n{cyan}{rule}{reset}")
        print(f"{bold}[Étape {step}/{TOTAL_STEPS}] {title}{reset}")
        print(f"{cyan}{rule}{reset}")

    def explain(self, text: str) -> None:
        dim, reset = self._c("\033[2m"), self._c("\033[0m")
        for paragraph in textwrap.dedent(text).strip("\n").split("\n"):
            print(f"{dim}› {paragraph}{reset}" if paragraph.strip() else "")

    def kv(self, label: str, value: Any, unit: str = "") -> None:
        suffix = f" {unit}" if unit else ""
        print(f"    {label:<42} {value}{suffix}")

    def note(self, text: str) -> None:
        print(f"  {text}")

    def warn(self, text: str) -> None:
        yellow, reset = self._c("\033[33m"), self._c("\033[0m")
        print(f"  {yellow}⚠ {text}{reset}")

    def ok(self, text: str) -> None:
        green, reset = self._c("\033[32m"), self._c("\033[0m")
        print(f"  {green}✓ {text}{reset}")

    def bold(self, text: str) -> str:
        return f"{self._c(chr(27) + '[1m')}{text}{self._c(chr(27) + '[0m')}"

    @contextmanager
    def timed(self, label: str) -> Iterator[None]:
        dim, reset = self._c("\033[2m"), self._c("\033[0m")
        start = time.perf_counter()
        yield
        elapsed = time.perf_counter() - start
        self.stage_times.append((label, elapsed))
        print(f"  {dim}⏱ {label} : {elapsed:.2f} s{reset}")

    def summary_table(self) -> None:
        if not self.stage_times:
            return
        width = max(len(label) for label, _ in self.stage_times)
        for label, elapsed in self.stage_times:
            print(f"    {label:<{width}}  {elapsed:>7.2f} s")
        total = sum(elapsed for _, elapsed in self.stage_times)
        print(f"    {'─' * (width + 12)}")
        print(f"    {'total chronométré':<{width}}  {total:>7.2f} s")


def _fmt_int(value: int) -> str:
    return f"{value:,}".replace(",", " ")


# ──────────────────────────────────────────────────────────────────────────
# Démonstration de la recherche hybride sur des questions illustratives.
# ──────────────────────────────────────────────────────────────────────────


def demo_retrieval(
    r: Reporter,
    engine: RAGIndex,
    settings: Any,
    label: str,
    question: str,
    expected_pages: set[int] | None,
) -> None:
    r.note("")
    r.note(f"{r.bold(label)} — « {question} »")
    enriched = build_retrieval_query(question)
    if enriched != question:
        extra = enriched[len(question) :].strip()
        r.note(f"    glossaire métier ajouté : « {extra[:140]}{'…' if len(extra) > 140 else ''} »")

    lexical_results = engine.retrieve(enriched, top_k=5, query_embedding=None)
    r.note("    Recherche LEXICALE seule (TF-IDF mots 78 % + caractères 22 %) :")
    for rank, item in enumerate(lexical_results[:3], start=1):
        r.note(
            f"      {rank}. page PDF {item['pdf_page']:<4} "
            f"score={item['score']:.4f}  recouvrement_mots_clés={item['keyword_overlap']}"
        )

    results_for_guard = lexical_results
    if engine.has_semantic_index and settings.semantic_retrieval:
        vector = embed_query(
            enriched, model=settings.embedding_model, cache_path=settings.embedding_cache_path
        )
        hybrid_results = engine.retrieve(
            enriched, top_k=5, query_embedding=vector, semantic_weight=settings.semantic_weight
        )
        r.note(
            "    Recherche HYBRIDE — le CLASSEMENT est décidé par Reciprocal Rank Fusion : "
            f"rrf = {1 - settings.semantic_weight:.2f}/(60+rang_lexical) "
            f"+ {settings.semantic_weight:.2f}/(60+rang_sémantique)"
        )
        for rank, item in enumerate(hybrid_results[:3], start=1):
            sem = item.get("semantic_score")
            sem_txt = f"{sem:.4f}" if sem is not None else "n/d"
            r.note(
                f"      {rank}. page PDF {item['pdf_page']:<4} "
                f"score_affiché={item['score']:.4f}  lexical={item['lexical_score']:.4f}  "
                f"sémantique={sem_txt}"
            )
        r.note(
            "      (le « score_affiché » est une moyenne pondérée lexical/sémantique, "
            "gardée lisible pour l'humain — ce n'est PAS le score RRF, qui ne compare que "
            "des rangs et sert uniquement à ORDONNER la liste ci-dessus ; c'est pourquoi "
            "cette colonne peut ne pas décroître strictement d'une ligne à l'autre.)"
        )
        results_for_guard = hybrid_results

    accepted = engine.is_relevant(
        results_for_guard, settings.min_relevance_score, settings.min_semantic_score
    )
    if accepted:
        r.ok("    Garde-fou de pertinence → ACCEPTÉ : la génération peut se fonder sur ces passages.")
    else:
        r.warn(
            "    Garde-fou de pertinence → REFUSÉ : l'assistant répondrait "
            "« information absente du corpus »."
        )

    if expected_pages is not None:
        top_page = int(results_for_guard[0]["pdf_page"]) if results_for_guard else None
        if expected_pages:
            mark = "✓" if top_page in expected_pages else "✗"
            r.note(
                f"    Vérité terrain (jeu d'évaluation) : pages attendues {sorted(expected_pages)} "
                f"— meilleur résultat : page {top_page} [{mark}]"
            )
        else:
            mark = "✓" if not accepted else "✗"
            r.note(f"    Vérité terrain : question hors corpus, refus attendu [{mark}]")


def print_metrics(r: Reporter, label: str, metrics: dict[str, Any]) -> None:
    r.note("")
    r.note(r.bold(f"Métriques — {label}"))
    r.kv("Hit@1", f"{metrics['hit_at_1']:.2%}")
    r.kv("Hit@3", f"{metrics['hit_at_3']:.2%}")
    r.kv("Hit@5", f"{metrics['hit_at_5']:.2%}")
    r.kv("Hit@12 (rappel des candidats du reranker)", f"{metrics['hit_at_12']:.2%}")
    r.kv("MRR (rang moyen inversé)", f"{metrics['mrr']:.4f}")
    r.kv("Acceptation des questions documentées", f"{metrics['answerable_acceptance']:.2%}")
    r.kv("Grounded Hit@5 (pertinent ET dans le top 5)", f"{metrics['grounded_hit_at_5']:.2%}")
    r.kv("Refus correct des questions hors corpus", f"{metrics['refusal_accuracy']:.2%}")
    for difficulty, score in metrics["success_by_difficulty"].items():
        r.kv(f"  dont difficulté « {difficulty} »", f"{score:.2%}")


# ──────────────────────────────────────────────────────────────────────────
# Programme principal
# ──────────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Exécute et explique tout le pipeline RAG BCM : configuration, "
            "corpus, indexation lexicale, indexation sémantique, recherche "
            "hybride, évaluation et génération de bout en bout."
        )
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore le cache et reconstruit l'index lexical ET sémantique depuis zéro.",
    )
    parser.add_argument(
        "--skip-semantic",
        action="store_true",
        help="Saute l'indexation/la recherche sémantique (démo lexicale seule, plus rapide).",
    )
    parser.add_argument(
        "--skip-eval",
        action="store_true",
        help="Saute l'étape d'évaluation quantitative (plus rapide).",
    )
    parser.add_argument(
        "--ask",
        type=str,
        default=None,
        help="Question personnalisée pour la démonstration de bout en bout finale.",
    )
    parser.add_argument(
        "--provider",
        choices=["extractive", "auto", "openai", "gemini", "ollama"],
        default="extractive",
        help=(
            "Fournisseur de génération pour la démo finale. 'extractive' (par défaut) "
            "ne nécessite aucune clé API ni appel réseau."
        ),
    )
    parser.add_argument("--language", choices=["fr", "ar"], default="fr")
    parser.add_argument("--no-color", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    r = Reporter(use_color=not args.no_color and sys.stdout.isatty())

    print(r.bold("BCM RAG — pipeline complet, expliqué pas à pas"))
    print(
        "Chaque étape correspond à ce que ferait un script d'entraînement ML : "
        "chargement des données, feature engineering, entraînement, inférence, "
        "validation quantitative."
    )

    # ── Étape 1 — Configuration ────────────────────────────────────────
    settings = get_settings()
    r.banner(1, "Configuration — les « hyperparamètres » du pipeline")
    r.explain(
        """
        Comme un script d'entraînement lit son fichier de config avant de lancer
        les epochs, `core/config.py` lit et valide `.env` avant toute exécution.
        Une valeur hors bornes arrête le programme ici, avant de gaspiller du
        temps de calcul sur une configuration invalide.
        """
    )
    r.kv("Profil d'exécution (APP_ENV)", settings.app_env)
    r.kv("Document source", settings.report_path.name)
    r.kv("Index persistant", settings.index_path)
    r.kv("Découpage des passages (chunk_size / overlap)", "1150 / 180 caractères")
    r.kv("TOP_K — passages finaux transmis à la génération", settings.top_k)
    r.kv("RETRIEVAL_CANDIDATES — candidats avant sélection", settings.retrieval_candidates)
    r.kv("SEMANTIC_RETRIEVAL activé", settings.semantic_retrieval)
    r.kv("SEMANTIC_WEIGHT — poids sémantique dans la fusion RRF", settings.semantic_weight)
    r.kv("MIN_RELEVANCE_SCORE — garde-fou lexical", settings.min_relevance_score)
    r.kv("MIN_SEMANTIC_SCORE — garde-fou sémantique", settings.min_semantic_score)
    r.kv("Modèle d'embedding local", settings.embedding_model)
    r.kv("GENERATION_PROVIDER (.env)", settings.generation_provider)

    # ── Étape 2 — Corpus ────────────────────────────────────────────────
    r.banner(2, "Chargement du corpus — les « données brutes »")
    r.explain(
        """
        Le corpus réunit deux types de sources : le Rapport annuel (texte PDF
        natif, extrait par pypdf) et les Lettres d'information (image -> PDF ->
        OCR Apple Vision, déjà pré-calculé et versionné dans data/lettres_information/ocr/).
        Chaque document devient une liste de « segments » citables : une page
        PDF ou une édition de lettre = une unité citable.

        Note : le corpus est chargé ici une seconde fois par `build()/load()` à
        l'étape suivante — la lecture est volontairement dupliquée pour montrer
        les statistiques des données brutes avant l'indexation, comme on
        inspecterait un jeu de données avant de l'entraîner.
        """
    )
    with r.timed("extraction du texte (pypdf + lecture des fichiers OCR)"):
        corpus = load_corpus(settings.report_path)
    total_segments = sum(len(document.segments) for document in corpus)
    r.kv("Documents chargés", len(corpus))
    r.kv("Segments (pages/unités citables) au total", _fmt_int(total_segments))
    by_type: dict[str, int] = defaultdict(int)
    for document in corpus:
        by_type[document.source_type] += len(document.segments)
    for source_type, count in sorted(by_type.items()):
        r.kv(f"  dont source « {source_type} »", count)
    for document in corpus[:6]:
        r.note(
            f"    - {document.doc_id:<28} {document.source_type:<7} "
            f"{len(document.segments):>3} page(s)  {document.title[:48]}"
        )
    if len(corpus) > 6:
        r.note(f"    … et {len(corpus) - 6} document(s) de plus")

    # ── Étape 3 — Indexation lexicale ──────────────────────────────────
    r.banner(3, "Indexation lexicale — chunking + « entraînement » TF-IDF")
    r.explain(
        """
        Chaque page est découpée en passages ("chunks") de ~1150 caractères
        avec un recouvrement de 180 caractères, pour ne jamais couper une idée
        en deux. Un second découpeur, ligne à ligne, cible spécifiquement les
        tableaux chiffrés afin de ne pas diluer une valeur isolée dans un
        paragraphe générique.

        Deux vectoriseurs TF-IDF sont ensuite ajustés sur l'ensemble des
        chunks — l'équivalent du `fit()` d'un modèle scikit-learn classique :
          • un vectoriseur MOTS (uni+bigrammes), qui porte 78 % du score
            lexical final ;
          • un vectoriseur CARACTÈRES (n-grammes 3-5), qui porte les 22 %
            restants et absorbe fautes de frappe, sigles et variantes.
        """
    )
    engine = RAGIndex(settings.report_path, settings.index_path)
    if args.force:
        r.note("Option --force : reconstruction complète, cache ignoré.")
        with r.timed("construction de l'index lexical (build, depuis zéro)"):
            metadata = engine.build()
    else:
        with r.timed("chargement / consolidation de l'index lexical (load)"):
            metadata = engine.load().metadata

    kind_counts = Counter(chunk.kind for chunk in engine.chunks)
    r.kv("Chunks indexés au total", _fmt_int(len(engine.chunks)))
    for kind, count in sorted(kind_counts.items()):
        r.kv(f"  dont kind='{kind}'", _fmt_int(count))
    r.kv("Vocabulaire TF-IDF mots (1-2 grammes)", _fmt_int(len(engine.word_vectorizer.vocabulary_)))
    r.kv("Vocabulaire TF-IDF caractères (3-5 grammes)", _fmt_int(len(engine.char_vectorizer.vocabulary_)))
    r.kv("Matrice mots (chunks × features)", f"{engine.word_matrix.shape}")
    word_sparsity = 100 * (
        1 - engine.word_matrix.nnz / (engine.word_matrix.shape[0] * engine.word_matrix.shape[1])
    )
    r.kv("Sparsité de la matrice mots", f"{word_sparsity:.2f} %")
    r.kv("Version de schéma de l'index", metadata.get("index_schema_version"))

    # ── Étape 4 — Indexation sémantique ────────────────────────────────
    r.banner(4, "Indexation sémantique — encodage vectoriel local")
    r.explain(
        """
        Chaque chunk est représenté par un vecteur dense de 384 dimensions,
        produit localement par le modèle multilingue
        `intfloat/multilingual-e5-small` (Sentence Transformers) — jamais
        envoyé à une API externe. Les passages sont encodés avec le préfixe
        "passage: ", les questions avec le préfixe "query: " : c'est la
        convention attendue par ce modèle E5 pour distinguer les deux rôles.
        """
    )
    if args.skip_semantic or not settings.semantic_retrieval:
        r.warn("Étape ignorée (--skip-semantic ou SEMANTIC_RETRIEVAL=false).")
    else:
        needs_build = (
            args.force
            or not engine.has_semantic_index
            or engine.embedding_model != settings.embedding_model
        )
        if needs_build:
            from api.embeddings import embed_documents

            r.note(
                f"Vectorisation de {_fmt_int(len(engine.chunks))} passages par lots de "
                f"{settings.embedding_batch_size} (premier chargement du modèle : quelques secondes)."
            )
            with r.timed("encodage des embeddings (SentenceTransformer.encode)"):
                matrix = embed_documents(
                    [chunk.text for chunk in engine.chunks],
                    model=settings.embedding_model,
                    cache_path=settings.embedding_cache_path,
                    batch_size=settings.embedding_batch_size,
                    show_progress=True,
                )
                engine.attach_semantic_embeddings(matrix, settings.embedding_model)
        else:
            with r.timed("chargement du modèle d'embedding depuis le cache local"):
                warm_embedding_model(settings.embedding_model, settings.embedding_cache_path)
            r.ok("Index sémantique déjà présent et à jour — réutilisation, pas de recalcul.")
        r.kv("Dimensions du vecteur", engine.semantic_matrix.shape[1])
        r.kv("Vecteurs stockés", _fmt_int(engine.semantic_matrix.shape[0]))
        footprint_mb = engine.semantic_matrix.nbytes / (1024 * 1024)
        r.kv("Empreinte mémoire de la matrice", f"{footprint_mb:.1f} Mo")

    # ── Étape 5 — Démonstration de la recherche hybride ────────────────
    r.banner(5, "Recherche hybride — « inférence » sur des questions illustratives")
    r.explain(
        """
        Trois cas représentatifs, tirés du jeu d'évaluation, illustrent le
        comportement du moteur : une question directe (vocabulaire proche du
        rapport), une paraphrase (vocabulaire différent — c'est le sémantique
        qui la rattrape), et une question hors corpus (le garde-fou doit la
        refuser).
        """
    )
    demo_retrieval(
        r, engine, settings,
        "1. Question directe",
        "Quel a été le taux de croissance du PIB réel en Mauritanie en 2025 ?",
        {5, 21, 121},
    )
    demo_retrieval(
        r, engine, settings,
        "2. Paraphrase (vocabulaire différent)",
        "De combien l'activité économique mauritanienne a-t-elle progressé en volume durant l'exercice ?",
        {5, 21, 121},
    )
    demo_retrieval(
        r, engine, settings,
        "3. Hors corpus (refus attendu)",
        "Quelle est la recette traditionnelle du sushi japonais ?",
        set(),
    )

    # ── Étape 6 — Évaluation quantitative ──────────────────────────────
    r.banner(6, "Évaluation quantitative — les « métriques de validation »")
    r.explain(
        """
        Comme la courbe de validation d'un entraînement, un jeu de 41 questions
        de référence (`evaluation/questions.jsonl`) mesure objectivement la
        qualité du retrieval : Hit@k (la bonne page apparaît-elle dans les k
        premiers résultats ?), MRR (rang moyen inversé de la première bonne
        page), et le taux de refus correct sur les questions volontairement
        hors corpus.
        """
    )
    if args.skip_eval:
        r.warn("Étape ignorée (--skip-eval).")
    else:
        cases = evaluate_retrieval.load_cases(PROJECT_ROOT / "evaluation" / "questions.jsonl")
        with r.timed(f"évaluation lexicale seule ({len(cases)} cas)"):
            report_lexical = evaluate_retrieval.evaluate(
                engine, cases, top_k=12, min_score=settings.min_relevance_score, mode="lexical"
            )
        print_metrics(r, "LEXICAL seul (baseline)", report_lexical["metrics"])

        if engine.has_semantic_index and settings.semantic_retrieval and not args.skip_semantic:
            with r.timed(f"évaluation hybride ({len(cases)} cas)"):
                report_hybrid = evaluate_retrieval.evaluate(
                    engine,
                    cases,
                    top_k=12,
                    min_score=settings.min_relevance_score,
                    mode="hybrid",
                    embedding_model=settings.embedding_model,
                    embedding_cache_path=settings.embedding_cache_path,
                    semantic_weight=settings.semantic_weight,
                    min_semantic_score=settings.min_semantic_score,
                )
            print_metrics(r, "HYBRIDE (production)", report_hybrid["metrics"])
            delta = (
                report_hybrid["metrics"]["hit_at_5"] - report_lexical["metrics"]["hit_at_5"]
            )
            r.note("")
            r.note(f"  Gain de Hit@5 apporté par le signal sémantique : {delta:+.2%}")

    # ── Étape 7 — Génération de bout en bout ───────────────────────────
    r.banner(7, "Génération de bout en bout — la « prédiction finale »")
    r.explain(
        """
        Cette étape reproduit, en miniature, ce que fait `/api/ask` : recherche
        → sélection des meilleurs passages → ajout des voisins de contexte →
        rédaction d'une réponse strictement fondée sur ces passages, avec
        citation `[p. PDF N]`. Par défaut, le provider "extractive" est
        utilisé : aucune clé API n'est nécessaire, la réponse est composée
        directement à partir des meilleurs extraits, sans rédaction par un LLM.
        """
    )
    demo_question = args.ask or "Quel a été le taux de croissance du PIB réel en Mauritanie en 2025 ?"
    r.note(f"{r.bold('Question posée :')} {demo_question}")
    enriched_question = build_retrieval_query(demo_question)
    embedding = None
    if engine.has_semantic_index and settings.semantic_retrieval and not args.skip_semantic:
        embedding = embed_query(
            enriched_question, model=settings.embedding_model, cache_path=settings.embedding_cache_path
        )
    with r.timed("recherche + sélection des passages"):
        search_results = engine.retrieve(
            enriched_question,
            top_k=settings.retrieval_candidates,
            query_embedding=embedding,
            semantic_weight=settings.semantic_weight,
        )
        top_results = engine.decorate(search_results[: settings.top_k], args.language)
        generation_context = engine.decorate(
            engine.expand_with_neighbors(top_results, max_results=12), args.language
        )

    if not engine.is_relevant(search_results, settings.min_relevance_score, settings.min_semantic_score):
        r.warn("Garde-fou : aucun passage suffisamment pertinent — la question serait refusée ici.")
    else:
        provider = args.provider
        if provider == "auto":
            provider = resolve_provider(settings)
            r.note(f"  Mode 'auto' résolu vers le provider : {provider}")
        try:
            with r.timed(f"génération de la réponse (provider={provider})"):
                answer = answer_with_provider(
                    provider, demo_question, generation_context, [], settings, args.language
                )
        except Exception as exc:  # provider distant indisponible ou mal configuré
            r.warn(
                f"Provider '{provider}' indisponible ({type(exc).__name__}) — "
                "repli sur le mode extractif, comme le ferait l'API en production."
            )
            answer = answer_with_provider(
                "extractive", demo_question, generation_context, [], settings, args.language
            )

        print()
        print(f"  {r.bold('Réponse générée :')}")
        for line in textwrap.wrap(answer, width=88):
            print(f"    {line}")
        print()
        r.note("  Sources mobilisées :")
        for item in top_results[:5]:
            citation = item.get("citation", f"p. PDF {item['pdf_page']}")
            r.note(f"    - {citation}  (score={item['score']:.4f})")

    # ── Étape 8 — Bilan ─────────────────────────────────────────────────
    r.banner(8, "Bilan de l'exécution")
    r.explain(
        """
        Comme le récapitulatif de fin d'entraînement (temps total, métriques
        finales, artefacts sauvegardés), voici la synthèse de ce run.
        """
    )
    r.summary_table()
    if settings.index_path.exists():
        size_mb = settings.index_path.stat().st_size / (1024 * 1024)
        r.kv("Index persistant écrit sur disque", settings.index_path)
        r.kv("Taille de l'index", f"{size_mb:.1f} Mo")
    r.kv("Temps total de l'exécution", f"{time.perf_counter() - r._run_started:.1f} s")

    print()
    print("Pour aller plus loin :")
    print("  - Reconstruire entièrement depuis zéro :   python scripts/pipeline_walkthrough.py --force")
    print("  - Démo lexicale rapide (sans embeddings) : python scripts/pipeline_walkthrough.py --skip-semantic --skip-eval")
    print("  - Poser votre propre question :             python scripts/pipeline_walkthrough.py --ask \"...\" --provider auto")
    print("  - Servir l'API réelle :                     ./run.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
