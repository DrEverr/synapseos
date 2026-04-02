"""Document upload and ingestion endpoints."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile

from synapse.web.deps import get_graph, get_settings_dep

logger = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])

# In-memory ingestion task tracker
_tasks: dict[str, dict] = {}


@router.get("/documents")
def list_documents(graph=Depends(get_graph)):
    return graph.get_documents()


@router.post("/documents/upload")
async def upload_document(file: UploadFile, settings=Depends(get_settings_dep)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return {"error": "Only PDF files are supported"}

    # Save to temp file
    tmp = Path(tempfile.mkdtemp()) / file.filename
    content = await file.read()
    tmp.write_bytes(content)

    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "running", "filename": file.filename, "detail": "Starting..."}

    # Run ingestion in background
    asyncio.create_task(_run_ingestion(task_id, str(tmp), settings))

    return {"task_id": task_id, "filename": file.filename}


@router.get("/ingestion/{task_id}")
def ingestion_status(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        return {"status": "not_found"}
    return task


async def _run_ingestion(task_id: str, pdf_path: str, settings) -> None:
    """Run document ingestion in background."""
    try:
        _tasks[task_id]["detail"] = "Extracting document structure..."

        from synapse.extraction.pipeline import ingest_files

        result = await ingest_files(
            paths=[pdf_path],
            settings=settings,
            reset=False,
            dry_run=False,
        )

        _tasks[task_id] = {
            "status": "done",
            "filename": _tasks[task_id]["filename"],
            "detail": "Ingestion complete",
            "documents": result.get("documents_processed", 0),
            "entities": result.get("total_entities", 0),
            "relationships": result.get("total_relationships", 0),
            "errors": len(result.get("errors", [])),
        }
    except Exception as e:
        logger.error("Ingestion failed for task %s: %s", task_id, e)
        _tasks[task_id] = {
            "status": "error",
            "filename": _tasks[task_id].get("filename", ""),
            "detail": str(e),
        }
    finally:
        # Cleanup temp file
        try:
            Path(pdf_path).unlink(missing_ok=True)
            Path(pdf_path).parent.rmdir()
        except Exception:
            pass
