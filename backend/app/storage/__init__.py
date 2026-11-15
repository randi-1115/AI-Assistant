"""私有存储实现。"""

from functools import lru_cache

from app.storage.base import (
    EmptyFileError,
    FileTooLargeError,
    StorageBackend,
    StorageError,
    StoredObject,
)
from app.storage.local_storage import LocalStorage


@lru_cache
def get_storage() -> StorageBackend:
    """默认本地存储。测试或将来换云存储时替换这个依赖即可。"""
    return LocalStorage()


__all__ = [
    "EmptyFileError",
    "FileTooLargeError",
    "LocalStorage",
    "StorageBackend",
    "StorageError",
    "StoredObject",
    "get_storage",
]
