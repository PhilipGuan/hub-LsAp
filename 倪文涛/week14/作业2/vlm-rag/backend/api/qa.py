from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from backend.models.qa import Answer, Image, MAX_IMAGES, MAX_IMAGE_BYTES, MAX_QUESTION_CHARS, Question


router = APIRouter(tags=["qa"])


@router.post("/ask")
def ask(
    request: Request,
    text: Annotated[str, Form()] = "",
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> Answer:
    text = text.strip()
    if not text and not images:
        raise HTTPException(400, "请输入问题或上传图片。")
    if len(text) > MAX_QUESTION_CHARS or len(images or []) > MAX_IMAGES:
        raise HTTPException(400, "每次最多输入 4000 个字符和 4 张图片。")
    try:
        inputs = [Image.from_bytes(file.file.read(MAX_IMAGE_BYTES + 1)) for file in images or []]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return request.app.state.qa.ask(Question(text, inputs))
