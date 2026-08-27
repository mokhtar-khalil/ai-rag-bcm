"""Construction et interrogation de l'index hybride du rapport annuel BCM.

La recherche combine des mots, des fragments de caractères et, lorsqu'il est
activé, un vecteur sémantique. L'index reste toujours limité au PDF fourni.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from api.sources import (
    SOURCE_TYPE_PDF,
    Document,
    build_registry,
    citation_label,
    clean_pdf_page,
    corpus_fingerprint,
    file_checksum,
    load_corpus,
)


FRENCH_STOP_WORDS = {
    "a", "afin", "ainsi", "au", "aucun", "aux", "avec", "ce", "ces", "cet",
    "cette", "comme", "dans", "de", "des", "du", "elle", "en", "entre", "est",
    "et", "il", "ils", "je", "la", "le", "les", "leur", "leurs", "lui", "mais",
    "ne", "nos", "notre", "nous", "on", "ont", "ou", "par", "pas", "pour", "qu",
    "que", "quel", "quelle", "quelles", "quels", "qui", "sa", "se", "ses", "son",
    "sont", "sur", "un", "une", "vos", "votre", "vous", "y", "ete", "etre",
}

INDEX_SCHEMA_VERSION = 10


@dataclass(frozen=True)
class Chunk:
    """Passage indexé, rattaché à son unité citable et à sa source d'origine.

    `pdf_page` reste la clé entière de l'unité citable : le numéro de page pour
    un rapport PDF, un identifiant réservé (>= WEB_UNIT_OFFSET) pour une page du
    site bcm.mr. Les champs de source permettent de citer une URL publique
    plutôt qu'un numéro de page lorsque le passage ne vient pas d'un PDF.
    """
    chunk_id: int
    pdf_page: int
    text: str
    kind: str = "standard"
    focus_text: str = ""
    doc_id: str = ""
    source_type: str = SOURCE_TYPE_PDF
    url: str = ""
    title: str = ""


class RAGIndex:
    """Index hybride TF-IDF, entièrement limité au PDF fourni."""

    def __init__(
        self,
        report_path: Path,
        index_path: Path,
        chunk_size: int = 1150,
        overlap: int = 180,
    ) -> None:
        """Prépare un index vide associé au rapport et au fichier de persistance."""
        self.report_path = Path(report_path)
        self.index_path = Path(index_path)
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.chunks: list[Chunk] = []
        self.word_vectorizer: TfidfVectorizer | None = None
        self.char_vectorizer: TfidfVectorizer | None = None
        self.word_matrix: Any = None
        self.char_matrix: Any = None
        self.semantic_matrix: np.ndarray | None = None
        self.embedding_model: str | None = None
        self.metadata: dict[str, Any] = {}
        self.manifest: list[dict[str, Any]] = []
        self.source_registry: dict[int, dict[str, Any]] = {}

    @property
    def has_semantic_index(self) -> bool:
        """Vérifie que chaque passage possède bien un vecteur sémantique."""
        return self.semantic_matrix is not None and len(self.semantic_matrix) == len(self.chunks)

    @staticmethod
    def _sha256(path: Path) -> str:
        """Calcule l'empreinte du PDF afin de détecter un index devenu obsolète."""
        return file_checksum(path)

    _clean_page = staticmethod(clean_pdf_page)

    def _chunk_page(
        self,
        text: str,
        pdf_page: int,
        first_id: int,
        source: dict[str, str] | None = None,
    ) -> list[Chunk]:
        """Découpe une page en passages chevauchants pour préserver le contexte."""
        if not text:
            return []
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not paragraphs:
            paragraphs = [text]

        chunks: list[Chunk] = []
        current = ""
        chunk_id = first_id

        def add(piece: str) -> None:
            """Ajoute un passage assez long et attribue son identifiant suivant."""
            nonlocal chunk_id
            piece = piece.strip()
            # Certaines pages visuelles n'exposent nativement que leur titre
            # (le tableau ou le schéma est une image). Les conserver dès 60
            # caractères permet de retrouver leur page avant le traitement OCR.
            if len(piece) >= 60:
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        pdf_page=pdf_page,
                        text=piece,
                        **(source or {}),
                    )
                )
                chunk_id += 1

        for paragraph in paragraphs:
            if len(paragraph) > self.chunk_size:
                if current:
                    add(current)
                    current = ""
                start = 0
                while start < len(paragraph):
                    end = min(start + self.chunk_size, len(paragraph))
                    if end < len(paragraph):
                        boundary = paragraph.rfind(". ", start + self.chunk_size // 2, end)
                        if boundary > start:
                            end = boundary + 1
                    add(paragraph[start:end])
                    if end >= len(paragraph):
                        break
                    start = max(end - self.overlap, start + 1)
                continue

            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                add(current)
                tail = current[-self.overlap :]
                current = f"{tail}\n\n{paragraph}".strip()
        if current:
            add(current)
        return chunks

    def _table_line_chunks(
        self,
        text: str,
        pdf_page: int,
        first_id: int,
        source: dict[str, str] | None = None,
    ) -> list[Chunk]:
        """Ajoute des passages fins pour éviter de diluer une ligne de tableau."""
        chunks: list[Chunk] = []
        context: list[str] = []
        active_header = ""
        chunk_id = first_id
        for raw_line in text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                continue
            numeric_values = re.findall(
                r"(?<!\w)[+-]?\(?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:,\d+)?%?\)?",
                line,
            )
            has_letters = bool(re.search(r"[A-Za-zÀ-ÿ]{4,}", line))
            header_years = re.findall(r"\b(?:19|20)\d{2}\b", line)
            year_header = (
                len(set(header_years)) >= 2
                and any(
                    marker in line.casefold()
                    for marker in ("chiffres", "indicateur", "données", "donnees")
                )
            )
            if year_header:
                active_header = line
                continue
            heading_like = (
                has_letters
                and len(numeric_values) <= 1
                and not line.endswith((".", ";"))
                and (
                    len(line) <= 65
                    or bool(
                        re.match(
                            r"^(?:tableau|graphique|note|\d+(?:\.\d+)+)",
                            line,
                            flags=re.IGNORECASE,
                        )
                    )
                )
            )
            if heading_like:
                context.append(line)
                context = context[-4:]
            if len(numeric_values) < 2 or not has_letters or len(line) < 20:
                continue
            label_context = context[-3:]
            label_fragment = re.split(
                r"(?<!\w)[+-]?\(?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)",
                line,
                maxsplit=1,
            )[0].strip(" :-")
            focus_context = (
                [label_context[-1]]
                if label_context and label_fragment[:1].islower()
                else []
            )
            enriched = "\n".join(
                [*([active_header] if active_header else []), *label_context, line]
            ).strip()
            if len(enriched) < 80:
                continue
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    pdf_page=pdf_page,
                    text=enriched,
                    kind="table_row",
                    focus_text="\n".join([*focus_context, line]),
                    **(source or {}),
                )
            )
            chunk_id += 1
        return chunks

    def build(self, documents: list[Document] | None = None) -> dict[str, Any]:
        """Indexe les documents fournis, ou le seul rapport PDF par défaut."""
        corpus = (
            list(documents) if documents is not None else load_corpus(self.report_path)
        )
        if not corpus:
            raise RuntimeError("Aucun document à indexer.")

        chunks: list[Chunk] = []
        for document in corpus:
            source = {
                "doc_id": document.doc_id,
                "source_type": document.source_type,
                "url": document.url,
                "title": document.title,
            }
            for segment in document.segments:
                if not segment.text:
                    continue
                chunks.extend(
                    self._chunk_page(segment.text, segment.unit, len(chunks), source)
                )
                # Le découpage ligne à ligne vise les tableaux chiffrés des
                # rapports ; une page éditoriale du site n'en contient pas.
                if document.source_type == SOURCE_TYPE_PDF:
                    chunks.extend(
                        self._table_line_chunks(
                            segment.text, segment.unit, len(chunks), source
                        )
                    )

        if not chunks:
            raise RuntimeError("Aucun texte exploitable n'a été extrait des documents.")

        texts = [chunk.text for chunk in chunks]
        word_vectorizer = TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            stop_words=sorted(FRENCH_STOP_WORDS),
            ngram_range=(1, 2),
            min_df=1,
            max_df=0.98,
            sublinear_tf=True,
            max_features=70000,
        )
        char_vectorizer = TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            analyzer="char_wb",
            ngram_range=(3, 5),
            min_df=2,
            sublinear_tf=True,
            max_features=60000,
        )
        word_matrix = word_vectorizer.fit_transform(texts)
        char_matrix = char_vectorizer.fit_transform(texts)

        pdf_documents = [
            document for document in corpus if document.source_type == SOURCE_TYPE_PDF
        ]
        metadata = {
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "report_title": pdf_documents[0].title if pdf_documents else corpus[0].title,
            "report_file": self.report_path.name,
            "report_sha256": pdf_documents[0].checksum if pdf_documents else "",
            "pdf_pages": sum(len(document.segments) for document in pdf_documents),
            "documents": len(corpus),
            "pages_par_source": {
                source_type: sum(
                    len(document.segments)
                    for document in corpus
                    if document.source_type == source_type
                )
                for source_type in sorted({item.source_type for item in corpus})
            },
            "chunks": len(chunks),
            "semantic_index": False,
        }
        # L'empreinte n'est enregistrée que pour le corpus par défaut : un corpus
        # injecté (tests, ingestion expérimentale) ne correspond à aucun fichier.
        metadata["corpus_fingerprint"] = (
            corpus_fingerprint(self.report_path) if documents is None else {}
        )
        self.manifest = [document.manifest() for document in corpus]
        self.source_registry = build_registry(corpus)
        payload = {
            "metadata": metadata,
            "manifest": self.manifest,
            "source_registry": self.source_registry,
            "chunks": [asdict(chunk) for chunk in chunks],
            "word_vectorizer": word_vectorizer,
            "char_vectorizer": char_vectorizer,
            "word_matrix": word_matrix,
            "char_matrix": char_matrix,
        }
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(payload, self.index_path, compress=3)
        self._load_payload(payload)
        return metadata

    def _load_payload(self, payload: dict[str, Any]) -> None:
        """Restaure en mémoire les composants sérialisés de l'index."""
        self.metadata = payload["metadata"]
        self.manifest = payload.get("manifest", [])
        self.source_registry = payload.get("source_registry", {})
        self.chunks = [Chunk(**item) for item in payload["chunks"]]
        self.word_vectorizer = payload["word_vectorizer"]
        self.char_vectorizer = payload["char_vectorizer"]
        self.word_matrix = payload["word_matrix"]
        self.char_matrix = payload["char_matrix"]
        semantic_matrix = payload.get("semantic_matrix")
        self.semantic_matrix = (
            np.asarray(semantic_matrix, dtype=np.float32)
            if semantic_matrix is not None
            else None
        )
        self.embedding_model = payload.get("embedding_model")

    def _payload(self) -> dict[str, Any]:
        """Assemble l'état de l'index dans un format enregistrable par joblib."""
        return {
            "metadata": self.metadata,
            "manifest": self.manifest,
            "source_registry": self.source_registry,
            "chunks": [asdict(chunk) for chunk in self.chunks],
            "word_vectorizer": self.word_vectorizer,
            "char_vectorizer": self.char_vectorizer,
            "word_matrix": self.word_matrix,
            "char_matrix": self.char_matrix,
            "semantic_matrix": self.semantic_matrix,
            "embedding_model": self.embedding_model,
        }

    def attach_semantic_embeddings(self, matrix: np.ndarray, model: str) -> None:
        """Normalise et persiste les vecteurs sémantiques associés aux passages."""
        values = np.asarray(matrix, dtype=np.float32)
        if values.ndim != 2 or values.shape[0] != len(self.chunks):
            raise ValueError(
                "La matrice sémantique doit contenir exactement un vecteur par passage."
            )
        if not np.isfinite(values).all():
            raise ValueError("La matrice sémantique contient une valeur invalide.")
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        if np.any(norms == 0):
            raise ValueError("Un vecteur sémantique ne peut pas être nul.")
        self.semantic_matrix = values / norms
        self.embedding_model = model
        self.metadata["semantic_index"] = True
        self.metadata["embedding_dimensions"] = int(values.shape[1])
        joblib.dump(self._payload(), self.index_path, compress=3)

    def load(
        self,
        rebuild_if_stale: bool = True,
        documents: list[Document] | None = None,
    ) -> "RAGIndex":
        """Charge l'index et le reconstruit si le schéma ou une source a changé."""
        if not self.index_path.exists():
            self.build(documents)
            return self
        payload = joblib.load(self.index_path)
        stale = payload["metadata"].get("index_schema_version") != INDEX_SCHEMA_VERSION
        if not stale:
            if documents is None:
                # Corpus par défaut : comparer les empreintes de fichiers suffit
                # et évite de réextraire le texte de chaque document.
                stale = payload["metadata"].get(
                    "corpus_fingerprint"
                ) != corpus_fingerprint(self.report_path)
            else:
                stale = [document.manifest() for document in documents] != payload.get(
                    "manifest", []
                )
        if rebuild_if_stale and stale:
            self.build(documents)
        else:
            self._load_payload(payload)
        return self

    @staticmethod
    def _keywords(text: str) -> set[str]:
        """Extrait les mots significatifs employés dans les contrôles de pertinence."""
        words = re.findall(r"[a-zà-ÿ0-9]{3,}", text.casefold())
        return {word for word in words if word not in FRENCH_STOP_WORDS}

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        query_embedding: np.ndarray | None = None,
        semantic_weight: float = 0.55,
    ) -> list[dict[str, Any]]:
        """Retourne les meilleurs passages après fusion lexicale et sémantique."""
        if not query.strip():
            return []
        if self.word_vectorizer is None or self.char_vectorizer is None:
            raise RuntimeError("L'index doit être chargé avant la recherche.")

        query_keywords = self._keywords(query)
        word_query = self.word_vectorizer.transform([query])
        char_query = self.char_vectorizer.transform([query])
        lexical_scores = 0.78 * (self.word_matrix @ word_query.T).toarray().ravel()
        lexical_scores += 0.22 * (self.char_matrix @ char_query.T).toarray().ravel()
        focus_factors = np.ones(len(self.chunks), dtype=np.float32)
        for idx, chunk in enumerate(self.chunks):
            if chunk.kind != "table_row":
                continue
            focus_overlap = len(query_keywords & self._keywords(chunk.focus_text))
            if focus_overlap == 0:
                focus_factors[idx] = 0.20
            elif focus_overlap == 1:
                focus_factors[idx] = 0.55
        lexical_scores *= focus_factors
        # Les pages liminaires et la table des matières répètent de nombreux mots-clés
        # sans contenir la réponse. Une pénalité évite qu'elles dominent les résultats.
        for idx, chunk in enumerate(self.chunks):
            dot_leaders = chunk.text.count("....") >= 2
            if chunk.pdf_page <= 12 and dot_leaders:
                lexical_scores[idx] *= 0.12

        semantic_scores: np.ndarray | None = None
        ranking_scores = lexical_scores
        mode = "lexical"
        lexical_order = np.argsort(lexical_scores)[::-1]
        if query_embedding is not None and self.has_semantic_index:
            vector = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
            if vector.shape[0] != self.semantic_matrix.shape[1]:
                raise ValueError("La dimension du vecteur de requête ne correspond pas à l'index.")
            norm = float(np.linalg.norm(vector))
            if norm == 0 or not np.isfinite(norm):
                raise ValueError("Le vecteur de requête est invalide.")
            semantic_scores = self.semantic_matrix @ (vector / norm)
            semantic_scores = np.maximum(semantic_scores, 0.0)
            semantic_scores *= focus_factors
            for idx, chunk in enumerate(self.chunks):
                if chunk.pdf_page <= 12 and chunk.text.count("....") >= 2:
                    semantic_scores[idx] *= 0.12

            # Reciprocal Rank Fusion : les échelles lexicales et sémantiques ne
            # sont pas directement comparables, leurs rangs le sont.
            semantic_order = np.argsort(semantic_scores)[::-1]
            lexical_ranks = np.empty(len(self.chunks), dtype=np.int32)
            semantic_ranks = np.empty(len(self.chunks), dtype=np.int32)
            lexical_ranks[lexical_order] = np.arange(1, len(self.chunks) + 1)
            semantic_ranks[semantic_order] = np.arange(1, len(self.chunks) + 1)
            weight = min(max(float(semantic_weight), 0.0), 1.0)
            ranking_scores = (
                (1.0 - weight) / (60.0 + lexical_ranks)
                + weight / (60.0 + semantic_ranks)
            )
            mode = "hybrid"
        candidate_count = min(max(top_k * 6, 20), len(self.chunks))
        candidates = np.argpartition(ranking_scores, -candidate_count)[-candidate_count:]
        candidates = candidates[np.argsort(ranking_scores[candidates])[::-1]]

        selected: list[dict[str, Any]] = []
        per_page: dict[int, int] = {}
        table_pages: set[int] = set()
        for idx in candidates:
            chunk = self.chunks[int(idx)]
            page_count = per_page.get(chunk.pdf_page, 0)
            if page_count >= 2 and not (
                chunk.kind == "table_row" and chunk.pdf_page not in table_pages
            ):
                continue
            overlap = len(query_keywords & self._keywords(chunk.text))
            lexical_score = float(lexical_scores[idx])
            semantic_score = (
                float(semantic_scores[idx]) if semantic_scores is not None else None
            )
            display_score = lexical_score
            if semantic_score is not None:
                display_score = (1.0 - semantic_weight) * lexical_score + semantic_weight * semantic_score
            selected.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "pdf_page": chunk.pdf_page,
                    "text": chunk.text,
                    "score": round(float(display_score), 4),
                    "lexical_score": round(lexical_score, 4),
                    "semantic_score": (
                        round(semantic_score, 4) if semantic_score is not None else None
                    ),
                    "retrieval_mode": mode,
                    "kind": chunk.kind,
                    "keyword_overlap": overlap,
                    "query_keyword_count": len(query_keywords),
                }
            )
            per_page[chunk.pdf_page] = per_page.get(chunk.pdf_page, 0) + 1
            if chunk.kind == "table_row":
                table_pages.add(chunk.pdf_page)
            if len(selected) >= top_k:
                break

        # Les embeddings comprennent moins bien les lignes de tableaux que les
        # phrases naturelles. Pour une comparaison datée, on réserve une place
        # à la meilleure ligne lexicale afin que le reranker voie les valeurs
        # complètes au lieu d'un passage narratif ou tronqué.
        folded_query = query.casefold()
        comparison_query = (
            len(set(re.findall(r"\b(?:19|20)\d{2}\b", query))) >= 2
            and any(marker in folded_query for marker in ("compar", "évolution", "evolution", "écart", "ecart", "différence", "difference"))
        )
        if semantic_scores is not None and comparison_query and selected:
            selected_ids = {int(item["chunk_id"]) for item in selected}
            for idx in lexical_order:
                chunk = self.chunks[int(idx)]
                if chunk.kind != "table_row" or chunk.chunk_id in selected_ids:
                    continue
                focus_overlap = len(query_keywords & self._keywords(chunk.focus_text))
                lexical_score = float(lexical_scores[idx])
                if focus_overlap < 2 or lexical_score < 0.06:
                    continue
                semantic_score = float(semantic_scores[idx])
                display_score = (
                    (1.0 - semantic_weight) * lexical_score
                    + semantic_weight * semantic_score
                )
                reserved = {
                    "chunk_id": chunk.chunk_id,
                    "pdf_page": chunk.pdf_page,
                    "text": chunk.text,
                    "score": round(display_score, 4),
                    "lexical_score": round(lexical_score, 4),
                    "semantic_score": round(semantic_score, 4),
                    "retrieval_mode": mode,
                    "kind": chunk.kind,
                    "keyword_overlap": len(query_keywords & self._keywords(chunk.text)),
                    "query_keyword_count": len(query_keywords),
                    "reserved_table_evidence": True,
                }
                selected[min(7, len(selected) - 1)] = reserved
                break

        # Une correspondance lexicale exacte (souvent le titre d'un tableau ou
        # d'une section) peut être anormalement repoussée si son texte est très
        # court et que son embedding est moins descriptif que ceux des annexes.
        # Ce contrôle vient après la réservation des tableaux pour que les deux
        # preuves ne puissent pas se remplacer mutuellement.
        if semantic_scores is not None and selected:
            best_lexical_index = int(lexical_order[0])
            best_lexical_chunk = self.chunks[best_lexical_index]
            selected_ids = {int(item["chunk_id"]) for item in selected}
            best_overlap = len(
                query_keywords & self._keywords(best_lexical_chunk.text)
            )
            best_lexical_score = float(lexical_scores[best_lexical_index])
            if (
                best_lexical_chunk.chunk_id not in selected_ids
                and best_lexical_score >= 0.18
                and best_overlap >= 2
            ):
                semantic_score = float(semantic_scores[best_lexical_index])
                selected[-1] = {
                    "chunk_id": best_lexical_chunk.chunk_id,
                    "pdf_page": best_lexical_chunk.pdf_page,
                    "text": best_lexical_chunk.text,
                    "score": round(
                        (1.0 - semantic_weight) * best_lexical_score
                        + semantic_weight * semantic_score,
                        4,
                    ),
                    "lexical_score": round(best_lexical_score, 4),
                    "semantic_score": round(semantic_score, 4),
                    "retrieval_mode": mode,
                    "kind": best_lexical_chunk.kind,
                    "keyword_overlap": best_overlap,
                    "query_keyword_count": len(query_keywords),
                    "reserved_lexical_evidence": True,
                }
        return selected

    @staticmethod
    def fuse_ranked_results(
        result_sets: list[list[dict[str, Any]]],
        max_results: int = 24,
        rrf_constant: float = 60.0,
    ) -> list[dict[str, Any]]:
        """Fusionne plusieurs recherches sans favoriser une formulation particulière."""
        if not result_sets:
            return []
        fused_scores: dict[int, float] = {}
        best_items: dict[int, dict[str, Any]] = {}
        query_hits: dict[int, int] = {}
        for results in result_sets:
            seen_in_query: set[int] = set()
            for rank, item in enumerate(results, start=1):
                chunk_id = int(item["chunk_id"])
                fused_scores[chunk_id] = fused_scores.get(chunk_id, 0.0) + 1.0 / (
                    rrf_constant + rank
                )
                if chunk_id not in seen_in_query:
                    query_hits[chunk_id] = query_hits.get(chunk_id, 0) + 1
                    seen_in_query.add(chunk_id)
                current = best_items.get(chunk_id)
                if current is None or float(item["score"]) > float(current["score"]):
                    best_items[chunk_id] = dict(item)

        ordered_ids = sorted(
            fused_scores,
            key=lambda chunk_id: (
                fused_scores[chunk_id],
                query_hits[chunk_id],
                float(best_items[chunk_id]["score"]),
            ),
            reverse=True,
        )
        fused: list[dict[str, Any]] = []
        per_page: dict[int, int] = {}
        table_pages: set[int] = set()
        for chunk_id in ordered_ids:
            item = best_items[chunk_id]
            page = int(item["pdf_page"])
            is_table_row = item.get("kind") == "table_row"
            if per_page.get(page, 0) >= 2 and not (
                is_table_row and page not in table_pages
            ):
                continue
            item["fusion_score"] = round(fused_scores[chunk_id], 6)
            item["query_hits"] = query_hits[chunk_id]
            fused.append(item)
            per_page[page] = per_page.get(page, 0) + 1
            if is_table_row:
                table_pages.add(page)
            if len(fused) >= max_results:
                break
        return fused

    def expand_with_neighbors(
        self, results: list[dict[str, Any]], max_results: int = 10
    ) -> list[dict[str, Any]]:
        """Complète les résultats avec la suite d'une phrase ou d'un tableau tronqué."""
        expanded = list(results)
        known_ids = {int(item["chunk_id"]) for item in results}
        by_id = {chunk.chunk_id: chunk for chunk in self.chunks}
        for result in results[:3]:
            current_id = int(result["chunk_id"])
            # Les tableaux larges occupent souvent trois chunks. On privilégie
            # leur continuation, puis leur contexte précédent.
            for neighbor_id in (
                current_id + 1,
                current_id + 2,
                current_id - 1,
                current_id - 2,
            ):
                neighbor = by_id.get(neighbor_id)
                if (
                    neighbor is None
                    or neighbor.chunk_id in known_ids
                    or neighbor.pdf_page != result["pdf_page"]
                ):
                    continue
                expanded.append(
                    {
                        "chunk_id": neighbor.chunk_id,
                        "pdf_page": neighbor.pdf_page,
                        "text": neighbor.text,
                        "score": round(float(result["score"]) * 0.88, 4),
                        "keyword_overlap": result.get("keyword_overlap", 0),
                        "query_keyword_count": result.get("query_keyword_count", 0),
                        "neighbor": True,
                    }
                )
                known_ids.add(neighbor.chunk_id)
                if len(expanded) >= max_results:
                    return expanded
        return expanded

    def decorate(
        self, results: list[dict[str, Any]], language: str = "fr"
    ) -> list[dict[str, Any]]:
        """Joint à chaque passage le repère cité et l'origine de sa source.

        Les passages circulent ensuite tels quels jusqu'au générateur et jusqu'à
        la réponse HTTP : ni l'un ni l'autre n'a besoin de connaître le registre.
        """
        for item in results:
            entry = self.source_registry.get(int(item["pdf_page"]))
            if entry is None:
                # Index antérieur au registre : conserver l'ancien repère.
                item.setdefault("citation", f"p. PDF {item['pdf_page']}")
                item.setdefault("source_type", SOURCE_TYPE_PDF)
                continue
            item["citation"] = citation_label(
                entry["source_type"], entry["title"], entry["pdf_page"], language
            )
            item["source_type"] = entry["source_type"]
            item["source_title"] = entry["title"]
            item["source_url"] = entry["url"]
            item["source_page"] = entry["pdf_page"]
            item["source_date"] = entry.get("published_at", "")
        return results

    @staticmethod
    def is_relevant(
        results: list[dict[str, Any]],
        min_score: float,
        min_semantic_score: float = 0.88,
    ) -> bool:
        """Décide si les preuves dépassent les seuils minimaux avant génération."""
        if not results:
            return False
        best = results[0]
        lexical_score = float(best.get("lexical_score", best["score"]))
        semantic_score = best.get("semantic_score")
        if semantic_score is not None:
            semantic_score = float(semantic_score)
            lexical_evidence = lexical_score >= min_score and best["keyword_overlap"] >= 2
            semantic_evidence = semantic_score >= min_semantic_score and (
                best["keyword_overlap"] >= 2
                or semantic_score >= min(min_semantic_score + 0.04, 1.0)
            )
            return lexical_evidence or semantic_evidence
        return lexical_score >= min_score and (
            best["keyword_overlap"] >= 2 or lexical_score >= 0.18
        )
