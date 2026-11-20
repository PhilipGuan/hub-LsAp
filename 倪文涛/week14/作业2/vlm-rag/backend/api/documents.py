from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from backend.models.document import Document, Status, document_path
from backend.models.index_entry import asset_path


router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", status_code=202)
def upload(request: Request, file: Annotated[list[UploadFile], File()]) -> Document:
    if len(file) != 1:
        raise HTTPException(400, "每次只能上传一个文档。")
    upload = file[0]
    return request.app.state.documents.upload(upload.filename or "", upload.file)


@router.get("")
def list_documents(request: Request) -> list[Document]:
    return request.app.state.documents.list()


@router.post("/{document_id}/retry", status_code=202)
def retry(request: Request, document_id: UUID) -> Document:
    return request.app.state.documents.retry(document_id.hex)


@router.delete("/{document_id}", status_code=204)
def delete(request: Request, document_id: UUID) -> None:
    request.app.state.documents.delete(document_id.hex)


@router.get("/{document_id}/images/{image_id}")
def image(request: Request, document_id: UUID, image_id: str):
    documents = request.app.state.documents
    doc = documents.get(document_id.hex)
    if doc is None or doc.status != Status.READY:
        raise HTTPException(404, "图片不存在。")
    if not image_id.startswith(f"{document_id.hex}-") or not image_id.rsplit("-", 1)[1].isdigit():
        raise HTTPException(404, "图片不存在。")
    entry = documents.index.related([image_id]).get(image_id)
    if not entry or entry.kind != "image" or entry.document_id != doc.document_id or not entry.image_path:
        raise HTTPException(404, "图片不存在。")
    root = documents.data_dir
    try:
        path = asset_path(root, root / document_path(doc.document_id) / "parsed", entry.image_path)
    except FileNotFoundError:
        raise HTTPException(404, "图片不存在。") from None
    return FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
