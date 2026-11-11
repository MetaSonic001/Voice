"""Document endpoints: upload, inspect, replace, update, delete, roll back, re-index."""

import asyncio
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel

from app.api.deps import container, require_key
from app.container import Container

router = APIRouter(prefix="/v1", tags=["documents"])


def _options(sheets: str | None, product_prefixes: str | None) -> dict:
    options = {}
    if sheets:
        options["sheets"] = [s.strip() for s in sheets.split(",") if s.strip()]
    if product_prefixes is not None:
        options["product_prefixes"] = [p for p in product_prefixes.split("|") if p]
    return options


@router.post("/documents", status_code=201, dependencies=[Depends(require_key)])
async def upload(
    file: UploadFile = File(..., description="PDF (with a text layer), DOCX, XLSX, CSV, MD or TXT"),
    title: str | None = Form(None, description="Defaults to the file name"),
    category: str | None = Form(None, description="e.g. deposits, cards, loans, rates"),
    purpose: str = Form("knowledge", description="knowledge = indexed for answers; data = stored only"),
    effective_from: date | None = Form(None, description="Date the content is valid from (YYYY-MM-DD)"),
    sheets: str | None = Form(None, description="XLSX only: comma-separated sheet names to include"),
    product_prefixes: str | None = Form(None, description="Override product-title prefixes, '|'-separated"),
    c: Container = Depends(container),
):
    data = await file.read()
    return await asyncio.to_thread(c.documents.upload, file.filename or "upload", data, title=title, category=category,
                                   purpose=purpose, effective_from=effective_from, options=_options(sheets, product_prefixes))


@router.get("/documents")
def list_documents(include_deleted: bool = False, c: Container = Depends(container)):
    return c.documents.list_documents(include_deleted)


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, c: Container = Depends(container)):
    return c.documents.get(doc_id)


@router.get("/documents/{doc_id}/chunks")
def get_chunks(doc_id: str, version: int | None = Query(None, description="Version number; default = current"),
               c: Container = Depends(container)):
    return c.documents.chunks(doc_id, version)


class DocumentPatch(BaseModel):
    title: str | None = None
    category: str | None = None
    enabled: bool | None = None
    effective_from: date | None = None
    tags: dict | None = None  # doc_type, authority, effective_to, superseded_by (merged; "" clears a key)


@router.patch("/documents/{doc_id}", dependencies=[Depends(require_key)])
def update_document(doc_id: str, body: DocumentPatch, c: Container = Depends(container)):
    return c.documents.update(doc_id, **body.model_dump())


@router.delete("/documents/{doc_id}", dependencies=[Depends(require_key)])
def delete_document(doc_id: str, purge: bool = Query(False, description="true = remove files and chunks permanently"),
                    c: Container = Depends(container)):
    return c.documents.delete(doc_id, purge)


@router.post("/documents/{doc_id}/versions", status_code=201, dependencies=[Depends(require_key)])
async def replace_document(doc_id: str, file: UploadFile = File(...), effective_from: date | None = Form(None),
                           sheets: str | None = Form(None), product_prefixes: str | None = Form(None),
                           c: Container = Depends(container)):
    data = await file.read()
    options = _options(sheets, product_prefixes) or None
    return await asyncio.to_thread(c.documents.add_version, doc_id, file.filename or "upload", data, effective_from, options)


@router.post("/documents/{doc_id}/versions/{version_no}/activate", dependencies=[Depends(require_key)])
def activate_version(doc_id: str, version_no: int, c: Container = Depends(container)):
    return c.documents.activate(doc_id, version_no)


@router.post("/documents/{doc_id}/reindex", dependencies=[Depends(require_key)])
def reindex_document(doc_id: str, c: Container = Depends(container)):
    return c.documents.reindex(doc_id)


@router.post("/reindex", dependencies=[Depends(require_key)])
def reindex_all(c: Container = Depends(container)):
    return c.documents.reindex(None)


@router.get("/jobs")
def list_jobs(status: str | None = None, limit: int = 50, c: Container = Depends(container)):
    return c.documents.jobs(status, limit)
