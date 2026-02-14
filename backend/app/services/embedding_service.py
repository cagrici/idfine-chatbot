import logging

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading embedding model: %s", settings.embedding_model)
        _model = SentenceTransformer(settings.embedding_model)
        logger.info("Embedding model loaded successfully")
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts and return their vectors."""
    model = get_embedding_model()
    # E5 models need "query: " or "passage: " prefix
    if "e5" in settings.embedding_model.lower():
        texts = [f"passage: {t}" for t in texts]
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.tolist()


def embed_query(query: str) -> list[float]:
    """Embed a single query for search."""
    model = get_embedding_model()
    if "e5" in settings.embedding_model.lower():
        query = f"query: {query}"
    embedding = model.encode([query], normalize_embeddings=True, show_progress_bar=False)
    return embedding[0].tolist()
