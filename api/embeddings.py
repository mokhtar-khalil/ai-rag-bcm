"""Vectorisation locale des passages du rapport et des questions utilisateur."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np


@lru_cache(maxsize=4)
def _local_model(model_name: str, cache_path: str):
    """Charge une seule instance du modèle multilingue depuis le cache local."""
    from sentence_transformers import SentenceTransformer

    cache = Path(cache_path)
    cache.mkdir(parents=True, exist_ok=True)
    cached_model = cache / f"models--{model_name.replace('/', '--')}"
    return SentenceTransformer(
        model_name,
        cache_folder=cache_path,
        local_files_only=cached_model.exists(),
    )


def warm_embedding_model(model: str, cache_path: Path) -> None:
    """Charge le modèle au démarrage pour éviter l'attente sur la première question."""
    _local_model(model, str(cache_path))


def _encode_local(
    texts: Iterable[str],
    model: str,
    cache_path: Path,
    batch_size: int,
    prefix: str,
    show_progress: bool = False,
) -> np.ndarray:
    """Encode des textes normalisés avec le préfixe attendu par le modèle E5."""
    values = [f"{prefix}{text.strip()}" for text in texts]
    if not values or any(value == prefix for value in values):
        raise ValueError("Les textes à vectoriser doivent être non vides.")
    encoder = _local_model(model, str(cache_path))
    matrix = encoder.encode(
        values,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=show_progress,
    )
    return np.asarray(matrix, dtype=np.float32)


def embed_documents(
    texts: Iterable[str],
    model: str,
    cache_path: Path,
    batch_size: int = 64,
    show_progress: bool = False,
) -> np.ndarray:
    """Vectorise les passages localement ; aucun contenu n'est envoyé à une API."""
    return _encode_local(
        texts,
        model=model,
        cache_path=cache_path,
        batch_size=batch_size,
        prefix="passage: ",
        show_progress=show_progress,
    )


@lru_cache(maxsize=256)
def _cached_query_embedding(
    text: str, model: str, cache_path: str
) -> tuple[float, ...]:
    """Mémorise les vecteurs des questions répétées pour réduire la latence."""
    vector = _encode_local(
        [text],
        model=model,
        cache_path=Path(cache_path),
        batch_size=1,
        prefix="query: ",
    )[0]
    return tuple(float(value) for value in vector)


def embed_query(text: str, model: str, cache_path: Path) -> np.ndarray:
    """Vectorise une question localement et met le résultat en cache mémoire."""
    return np.asarray(
        _cached_query_embedding(text.strip(), model, str(cache_path)),
        dtype=np.float32,
    )
