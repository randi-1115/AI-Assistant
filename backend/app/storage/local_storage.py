"""本地私有文件存储。

关键约束：
- 文件名由服务端生成（uuid），绝不用用户上传的文件名拼路径。
- 写入先落到同目录的 .part 文件，成功后 os.replace 原子改名；超限或异常
  会删除临时文件，不留半截数据。
- path_for 严格校验目标位于根目录内，防止 storage_key 被污染后路径穿越。
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import anyio

from app.core.config import BACKEND_DIR, get_settings
from app.storage.base import (
    EmptyFileError,
    FileTooLargeError,
    StorageBackend,
    StorageError,
    StoredObject,
)

logger = logging.getLogger(__name__)

_WRITE_CHUNK_BYTES = 1024 * 1024


def _normalize_extension(extension: str) -> str:
    ext = extension.strip().lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    if len(ext) < 2 or "/" in ext or "\\" in ext or ".." in ext:
        raise StorageError(f"非法扩展名：{extension!r}")
    return ext


class LocalStorage(StorageBackend):
    def __init__(self, root: Path | str | None = None) -> None:
        raw = Path(root) if root is not None else Path(get_settings().upload_dir)
        if not raw.is_absolute():
            # UPLOAD_DIR=../data/uploads 相对的是 backend/ 目录。
            raw = BACKEND_DIR / raw
        self.root = raw.resolve()

    async def save(
        self,
        source: BinaryIO,
        *,
        user_id: int,
        extension: str,
        max_bytes: int,
    ) -> StoredObject:
        return await anyio.to_thread.run_sync(
            functools.partial(
                self._save_sync,
                source,
                user_id=user_id,
                extension=extension,
                max_bytes=max_bytes,
            )
        )

    def _save_sync(
        self,
        source: BinaryIO,
        *,
        user_id: int,
        extension: str,
        max_bytes: int,
    ) -> StoredObject:
        ext = _normalize_extension(extension)
        now = datetime.now(UTC)
        storage_key = f"{user_id}/{now:%Y%m}/{uuid.uuid4().hex}{ext}"
        target = self.path_for(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.part")

        digest = hashlib.sha256()
        size = 0
        try:
            source.seek(0)
            with temp.open("wb") as handle:
                while True:
                    block = source.read(_WRITE_CHUNK_BYTES)
                    if not block:
                        break
                    size += len(block)
                    if size > max_bytes:
                        raise FileTooLargeError(
                            f"文件超过大小限制（{max_bytes / (1024 * 1024):.0f}MB）"
                        )
                    digest.update(block)
                    handle.write(block)
            if size == 0:
                raise EmptyFileError("上传内容为空")
            os.replace(temp, target)
        except (FileTooLargeError, EmptyFileError):
            temp.unlink(missing_ok=True)
            raise
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        finally:
            # 上传临时文件在请求结束后由 Starlette 清理；这里不关它。
            pass

        try:
            os.chmod(target, 0o600)
        except OSError:  # pragma: no cover - Windows 上通常不支持
            logger.debug("设置文件权限失败 key=%s", storage_key)

        return StoredObject(
            storage_key=storage_key,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )

    async def delete(self, storage_key: str) -> None:
        await anyio.to_thread.run_sync(self._delete_sync, storage_key)

    def _delete_sync(self, storage_key: str) -> None:
        path = self.path_for(storage_key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - 磁盘异常
            raise StorageError(f"删除文件失败：{exc}") from exc

    def path_for(self, storage_key: str) -> Path:
        if not storage_key or storage_key.strip() != storage_key:
            raise StorageError("非法存储键")
        key_path = Path(storage_key)
        if key_path.is_absolute() or any(part in ("..", "") for part in key_path.parts):
            raise StorageError("非法存储键")
        candidate = (self.root / key_path).resolve()
        if not candidate.is_relative_to(self.root):
            raise StorageError("非法存储键")
        return candidate

    async def exists(self, storage_key: str) -> bool:
        return await anyio.to_thread.run_sync(lambda: self.path_for(storage_key).is_file())
