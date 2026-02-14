import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.exceptions import DocumentProcessingError, NotFoundError
from app.db.database import async_session, get_db
from app.dependencies import get_qdrant, require_admin
from app.models.document import Document
from app.models.user import User
from app.schemas.document import DocumentListResponse, DocumentResponse, DocumentUploadResponse
from app.services.document_service import DocumentService
from app.services.rag_engine import RAGEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_TYPES = {"pdf", "docx", "doc", "txt"}


async def _process_document_background(
    file_path: str,
    filename: str,
    file_type: str,
    category: str,
    uploaded_by: str,
    doc_id: str,
    qdrant_host: str,
    qdrant_port: int,
    source_group_id: str | None = None,
):
    """Process document in background task (extract text, chunk, embed, index)."""
    try:
        qdrant = AsyncQdrantClient(host=qdrant_host, port=qdrant_port)
        rag = RAGEngine(qdrant)
        await rag.ensure_collection()

        async with async_session() as db:
            service = DocumentService(db, rag)
            await service.ingest_file(
                file_path=file_path,
                filename=filename,
                file_type=file_type,
                category=category,
                uploaded_by=uploaded_by,
                doc_id=doc_id,
                source_group_id=source_group_id,
            )
            await db.commit()

        logger.info("Background document processing complete: %s", filename)
    except Exception as e:
        logger.error("Background document processing failed for %s: %s", filename, e)
        # Mark document as error
        try:
            async with async_session() as db:
                from sqlalchemy import update
                await db.execute(
                    update(Document).where(Document.id == uuid.UUID(doc_id)).values(status="error")
                )
                await db.commit()
        except Exception:
            pass


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    qdrant: Annotated[AsyncQdrantClient, Depends(get_qdrant)],
    source_group_id: str | None = None,
):
    rag = RAGEngine(qdrant)
    service = DocumentService(db, rag)
    documents, total = await service.list_documents(source_group_id=source_group_id)

    return DocumentListResponse(
        documents=[
            DocumentResponse(
                id=str(d.id),
                filename=d.filename,
                file_type=d.file_type,
                file_size=d.file_size,
                category=d.category,
                source_group_id=str(d.source_group_id) if d.source_group_id else None,
                source_group_name=d.source_group.name if d.source_group else None,
                status=d.status,
                chunk_count=d.chunk_count,
                created_at=d.created_at,
            )
            for d in documents
        ],
        total=total,
    )


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    qdrant: Annotated[AsyncQdrantClient, Depends(get_qdrant)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(...),
    category: str = Form(default="genel"),
    source_group_id: str = Form(default=None),
):
    """Upload and process a document (processing happens in background)."""
    if not file.filename:
        raise DocumentProcessingError("Dosya adı gerekli")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_TYPES:
        raise DocumentProcessingError(
            f"Desteklenmeyen dosya tipi: {ext}. İzin verilenler: {', '.join(ALLOWED_TYPES)}"
        )

    # Save file to disk
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_id = str(uuid.uuid4())
    file_path = upload_dir / f"{file_id}.{ext}"

    content = await file.read()
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise DocumentProcessingError(
            f"Dosya boyutu çok büyük. Maksimum: {settings.max_upload_size_mb}MB"
        )

    with open(file_path, "wb") as f:
        f.write(content)

    # Create document record immediately with "processing" status
    doc = Document(
        filename=file.filename,
        file_type=ext,
        file_size=len(content),
        category=category,
        source_group_id=uuid.UUID(source_group_id) if source_group_id else None,
        uploaded_by=uuid.UUID(str(user.id)),
        status="processing",
    )
    db.add(doc)
    await db.flush()
    doc_id = str(doc.id)
    await db.commit()

    # Launch processing in background - does NOT block the response
    asyncio.create_task(
        _process_document_background(
            file_path=str(file_path),
            filename=file.filename,
            file_type=ext,
            category=category,
            uploaded_by=str(user.id),
            doc_id=doc_id,
            qdrant_host=settings.qdrant_host,
            qdrant_port=settings.qdrant_port,
            source_group_id=source_group_id,
        )
    )

    return DocumentUploadResponse(
        id=doc_id,
        filename=file.filename,
        status="processing",
        message="Döküman yüklendi, arka planda işleniyor. Durumu kontrol etmek için listeyi yenileyin.",
    )


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    qdrant: Annotated[AsyncQdrantClient, Depends(get_qdrant)],
):
    """Delete a document and its vectors."""
    rag = RAGEngine(qdrant)
    service = DocumentService(db, rag)
    await service.delete_document(document_id)
    return {"status": "deleted", "document_id": document_id}


@router.post("/{document_id}/reindex")
async def reindex_document(
    document_id: str,
    user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    qdrant: Annotated[AsyncQdrantClient, Depends(get_qdrant)],
):
    """Re-index an existing document."""
    rag = RAGEngine(qdrant)
    await rag.ensure_collection()

    service = DocumentService(db, rag)
    doc = await service.reindex_document(document_id)

    return {
        "status": "reindexed",
        "document_id": document_id,
        "chunk_count": doc.chunk_count,
    }
