"""本地存储：key 生成、sha256、超限清理、路径穿越。"""

from __future__ import annotations

import hashlib
import io

import pytest

from app.storage.base import EmptyFileError, FileTooLargeError, StorageError
from app.storage.local_storage import LocalStorage


async def test_save_and_delete(tmp_path) -> None:
    storage = LocalStorage(root=tmp_path)
    payload = b"hello world"
    stored = await storage.save(
        io.BytesIO(payload), user_id=7, extension=".txt", max_bytes=100
    )

    assert stored.storage_key.startswith("7/")
    assert stored.storage_key.endswith(".txt")
    assert stored.size_bytes == len(payload)
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert await storage.exists(stored.storage_key)
    assert storage.path_for(stored.storage_key).read_bytes() == payload

    await storage.delete(stored.storage_key)
    assert not await storage.exists(stored.storage_key)
    # 幂等：再次删除不报错。
    await storage.delete(stored.storage_key)


async def test_oversize_leaves_no_part_file(tmp_path) -> None:
    storage = LocalStorage(root=tmp_path)
    with pytest.raises(FileTooLargeError):
        await storage.save(
            io.BytesIO(b"x" * 11), user_id=1, extension=".txt", max_bytes=10
        )
    assert not list(tmp_path.rglob("*.part"))


async def test_empty_file_rejected(tmp_path) -> None:
    storage = LocalStorage(root=tmp_path)
    with pytest.raises(EmptyFileError):
        await storage.save(
            io.BytesIO(b""), user_id=1, extension=".txt", max_bytes=10
        )


async def test_path_traversal_rejected(tmp_path) -> None:
    storage = LocalStorage(root=tmp_path)
    for key in ("../evil.txt", "a/../../evil.txt", str(tmp_path / "abs.txt")):
        with pytest.raises(StorageError):
            storage.path_for(key)


async def test_invalid_extension_rejected(tmp_path) -> None:
    storage = LocalStorage(root=tmp_path)
    with pytest.raises(StorageError):
        await storage.save(
            io.BytesIO(b"abc"), user_id=1, extension="../txt", max_bytes=100
        )
