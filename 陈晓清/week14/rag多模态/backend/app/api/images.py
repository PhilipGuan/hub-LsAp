# -*- coding: utf-8 -*-
"""受控图片读取：前端与后端分离后，前端不能直接读后端磁盘。"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..services.image_service import safe_image_path

router = APIRouter(prefix="/images", tags=["images"])


@router.get("")
def get_image(path: str = Query(...)):
    try:
        file_path = safe_image_path(path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except (FileNotFoundError, OSError):
        raise HTTPException(status_code=404, detail="图片不存在")

    media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )
