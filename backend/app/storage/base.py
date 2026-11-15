"""私有文件存储接口。

业务层只依赖本模块，不直接碰磁盘。将来换对象存储（OSS/S3）时新增一个实现
即可，FileService 不用改。storage_key 是内部相对键，永远不返回给客户端。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class StoredObject:
    storage_key: str
    size_bytes: int
    sha256: str


class StorageError(Exception):
    """存储层错误。上游统一映射成 500 storage_error。"""


class FileTooLargeError(StorageError):
    """写入过程中超过大小限制。上游映射成 413 file_too_large。"""


class EmptyFileError(StorageError):
    """上传内容为空。上游映射成 415 invalid_file_content。"""


class StorageBackend(ABC):
    @abstractmethod
    async def save(
        self,
        source: BinaryIO,
        *,
        user_id: int,
        extension: str,
        max_bytes: int,
    ) -> StoredObject:
        """把 source 写入私有存储，返回内部 key、实际大小与 sha256。"""

    @abstractmethod
    async def delete(self, storage_key: str) -> None:
        """删除对象；文件不存在时视为成功。"""

    @abstractmethod
    def path_for(self, storage_key: str) -> Path:
        """把内部 key 解析为物理路径，必须拒绝越界路径。"""

    @abstractmethod
    async def exists(self, storage_key: str) -> bool:
        ...
