import asyncio
import logging
import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.config import get_settings
from app.services.embedding_service import embed_query, embed_texts

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class RetrievedChunk:
    content: str
    score: float
    document_name: str
    chunk_index: int
    metadata: dict


class RAGEngine:
    def __init__(self, qdrant: AsyncQdrantClient):
        self.qdrant = qdrant
        self.collection = settings.qdrant_collection
        self._collection_empty: bool | None = None  # cached empty check

    async def is_collection_empty(self) -> bool:
        """Check if collection exists and has points. Cached after first call."""
        if self._collection_empty is not None:
            return self._collection_empty
        try:
            info = await self.qdrant.get_collection(self.collection)
            self._collection_empty = info.points_count == 0
        except Exception:
            self._collection_empty = True
        return self._collection_empty

    def invalidate_cache(self) -> None:
        """Call after indexing new documents to reset the empty check."""
        self._collection_empty = None

    async def ensure_collection(self) -> None:
        """Create the Qdrant collection if it doesn't exist."""
        collections = await self.qdrant.get_collections()
        existing = [c.name for c in collections.collections]

        if self.collection not in existing:
            await self.qdrant.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(
                    size=settings.embedding_dimension,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection: %s", self.collection)

        # Ensure payload index for source_group_id filtering
        try:
            await self.qdrant.create_payload_index(
                collection_name=self.collection,
                field_name="source_group_id",
                field_schema="keyword",
            )
        except Exception:
            pass  # Index may already exist

    async def index_chunks(
        self,
        chunks: list[str],
        document_id: str,
        document_name: str,
        category: str | None = None,
        source_group_id: str | None = None,
    ) -> list[str]:
        """Embed and index document chunks into Qdrant. Returns point IDs."""
        vectors = await asyncio.to_thread(embed_texts, chunks)
        point_ids = []

        points = []
        for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
            point_id = str(uuid.uuid4())
            point_ids.append(point_id)
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "content": chunk,
                        "document_id": document_id,
                        "document_name": document_name,
                        "chunk_index": i,
                        "category": category or "genel",
                        "source_group_id": source_group_id or "",
                    },
                )
            )

        # Batch upsert in groups of 100
        batch_size = 100
        for start in range(0, len(points), batch_size):
            batch = points[start : start + batch_size]
            await self.qdrant.upsert(
                collection_name=self.collection,
                points=batch,
            )

        logger.info(
            "Indexed %d chunks for document %s", len(chunks), document_name
        )
        self.invalidate_cache()
        return point_ids

    async def search(
        self,
        query: str,
        top_k: int = 10,
        score_threshold: float = 0.5,
        category: str | None = None,
        source_group_id: str | None = None,
    ) -> list[RetrievedChunk]:
        """Search for relevant chunks using semantic similarity."""
        # Skip embedding + search if collection is empty
        if await self.is_collection_empty():
            logger.debug("Skipping RAG search: collection is empty")
            return []

        query_vector = await asyncio.to_thread(embed_query, query)

        conditions = []
        if source_group_id:
            conditions.append(
                FieldCondition(
                    key="source_group_id", match=MatchValue(value=source_group_id)
                )
            )
        if category:
            conditions.append(
                FieldCondition(
                    key="category", match=MatchValue(value=category)
                )
            )
        search_filter = Filter(must=conditions) if conditions else None

        response = await self.qdrant.query_points(
            collection_name=self.collection,
            query=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=search_filter,
        )

        chunks = []
        for point in response.points:
            payload = point.payload or {}
            chunks.append(
                RetrievedChunk(
                    content=payload.get("content", ""),
                    score=point.score,
                    document_name=payload.get("document_name", ""),
                    chunk_index=payload.get("chunk_index", 0),
                    metadata=payload,
                )
            )

        return chunks

    async def delete_document_vectors(self, document_id: str) -> None:
        """Delete all vectors associated with a document."""
        await self.qdrant.delete(
            collection_name=self.collection,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="document_id", match=MatchValue(value=document_id)
                    )
                ]
            ),
        )
        logger.info("Deleted vectors for document %s", document_id)

    def build_context(
        self, chunks: list[RetrievedChunk], max_chunks: int = 5
    ) -> str:
        """Build a context string from retrieved chunks for LLM input."""
        selected = chunks[:max_chunks]
        if not selected:
            return ""

        context_parts = []
        for i, chunk in enumerate(selected, 1):
            source = chunk.document_name
            context_parts.append(
                f"[Kaynak {i}: {source}]\n{chunk.content}"
            )

        return "\n\n---\n\n".join(context_parts)

    def get_sources(self, chunks: list[RetrievedChunk], max_chunks: int = 5) -> list[dict]:
        """Extract source references from chunks."""
        seen = set()
        sources = []
        for chunk in chunks[:max_chunks]:
            key = (chunk.document_name, chunk.chunk_index)
            if key not in seen:
                seen.add(key)
                sources.append({
                    "document": chunk.document_name,
                    "chunk_index": chunk.chunk_index,
                    "score": round(chunk.score, 3),
                })
        return sources
