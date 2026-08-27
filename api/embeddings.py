"""Vectorisation locale des passages du rapport et des questions utilisateur."""

from __future__ import annotations

from collections import OrderedDict
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


# Cache mémoire des vecteurs de questions, partagé par les deux entrées. Un
# dictionnaire explicite permet de savoir ce qui manque avant d'encoder, ce
# qu'un lru_cache ne sait pas exposer sans déclencher le calcul.
_QUERY_CACHE: "OrderedDict[tuple[str, str, str], np.ndarray]" = OrderedDict()
_QUERY_CACHE_MAX = 256


def _remember(key: tuple[str, str, str], vector: np.ndarray) -> np.ndarray:
    """Mémorise un vecteur en écartant la question la plus anciennement utilisée."""
    _QUERY_CACHE[key] = vector
    _QUERY_CACHE.move_to_end(key)
    while len(_QUERY_CACHE) > _QUERY_CACHE_MAX:
        _QUERY_CACHE.popitem(last=False)
    return vector


def embed_query(text: str, model: str, cache_path: Path) -> np.ndarray:
    """Vectorise une question localement et met le résultat en cache mémoire."""
    return embed_queries([text], model, cache_path)[0]


def embed_queries(
    texts: list[str], model: str, cache_path: Path
) -> list[np.ndarray]:
    """Vectorise plusieurs reformulations en un seul passage du modèle.

    Chaque appel d'encodage porte un coût fixe. Les reformulations produites par
    le planificateur étaient vectorisées une par une : les regrouper supprime ce
    coût autant de fois qu'il y a de variantes.
    """
    keys = [(text.strip(), model, str(cache_path)) for text in texts]
    manquants = [key for key in dict.fromkeys(keys) if key not in _QUERY_CACHE]

    if manquants:
        matrix = _encode_local(
            [key[0] for key in manquants],
            model=model,
            cache_path=cache_path,
            batch_size=max(len(manquants), 1),
            prefix="query: ",
        )
        for key, vector in zip(manquants, matrix):
            _remember(key, np.asarray(vector, dtype=np.float32))

    return [_remember(key, _QUERY_CACHE[key]) for key in keys]

