"""Batch document ingestion script.

Usage:
    python -m scripts.ingest_documents /path/to/documents --category urunler
"""

import argparse
import asyncio
import os
from pathlib import Path

from app.config import get_settings
from app.db.database import async_session
from app.services.document_service import DocumentService, EXTRACTORS
from app.services.rag_engine import RAGEngine

from qdrant_client import AsyncQdrantClient


async def main(directory: str, category: str):
    settings = get_settings()

    qdrant = AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    rag = RAGEngine(qdrant)
    await rag.ensure_collection()

    dir_path = Path(directory)
    if not dir_path.is_dir():
        print(f"Error: {directory} is not a directory")
        return

    files = []
    for ext in EXTRACTORS:
        files.extend(dir_path.glob(f"*.{ext}"))

    if not files:
        print(f"No supported files found in {directory}")
        return

    print(f"Found {len(files)} files to process")

    async with async_session() as db:
        service = DocumentService(db, rag)

        for file_path in files:
            ext = file_path.suffix.lstrip(".")
            print(f"Processing: {file_path.name}...")

            try:
                doc = await service.ingest_file(
                    file_path=str(file_path),
                    filename=file_path.name,
                    file_type=ext,
                    category=category,
                )
                print(f"  -> {doc.chunk_count} chunks indexed")
            except Exception as e:
                print(f"  -> ERROR: {e}")

        await db.commit()

    print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch document ingestion")
    parser.add_argument("directory", help="Directory containing documents")
    parser.add_argument("--category", default="genel", help="Document category")
    args = parser.parse_args()

    asyncio.run(main(args.directory, args.category))
