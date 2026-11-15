"""文件上传、元数据查询与删除。

endpoint 只做参数绑定与响应序列化；校验、存储、解析都在 FileService。
/upload 必须声明在 /{file_id} 之前，避免被路径参数抢先匹配。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.db.session import get_db
from app.schemas.file import FileRead, FileUploadResponse
from app.services.file_service import FileService

router = APIRouter(prefix="/files", tags=["files"])


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    file: UploadFile = File(...),
    conversation_id: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> FileUploadResponse:
    service = FileService(db)
    attachment = await service.upload(
        user_id=user_id,
        upload=file,
        conversation_id=conversation_id,
    )
    return FileUploadResponse.model_validate(attachment)


@router.get("/{file_id}", response_model=FileRead)
async def get_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> FileRead:
    service = FileService(db)
    attachment = await service.get(file_id, user_id)
    return FileRead.model_validate(attachment)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> Response:
    service = FileService(db)
    await service.delete(file_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
