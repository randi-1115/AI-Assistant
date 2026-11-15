"""文件上传 / 查询 / 删除的端到端行为（真实 MySQL）。"""

from __future__ import annotations

import zipfile

from httpx import AsyncClient
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.storage import get_storage
from tests.integration.conftest import (
    attachment_row,
    chunk_rows,
    create_conversation,
    create_other_users_attachment,
    create_other_users_conversation,
    upload_file,
)


async def _attachment_count() -> int:
    async with SessionLocal() as session:
        return int(
            (await session.execute(text("SELECT COUNT(*) FROM attachments"))).scalar_one()
        )


async def test_upload_text_file_becomes_ready(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await upload_file(
        client,
        name="需求说明.txt",
        content="项目代号是蓝鲸。\n\n部署窗口安排在周五晚上。".encode(),
        conversation_id=conversation["id"],
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["original_name"] == "需求说明.txt"
    assert body["parse_status"] == "ready"
    assert body["supported_for_qa"] is True
    assert body["error_message"] is None

    row = await attachment_row(body["file_id"])
    assert row is not None
    assert row.conversation_id == conversation["id"]
    assert row.sha256
    chunks = await chunk_rows(row.id)
    assert chunks
    assert "蓝鲸" in chunks[0]["content"]
    assert get_storage().path_for(row.storage_key).is_file()


async def test_get_file_never_exposes_storage_path(client: AsyncClient):
    response = await upload_file(client)
    file_id = response.json()["file_id"]

    fetched = await client.get(f"/api/v1/files/{file_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["file_id"] == file_id
    assert "storage_key" not in body
    assert "storage_key" not in fetched.text
    assert str(get_settings().upload_dir) not in fetched.text


async def test_upload_sanitizes_unsafe_filename(client: AsyncClient):
    response = await upload_file(client, name=r"..\..\evil.txt")
    assert response.status_code == 201, response.text
    assert response.json()["original_name"] == "evil.txt"
    assert "/" not in response.json()["original_name"]


async def test_delete_removes_row_chunks_and_disk_file(client: AsyncClient):
    response = await upload_file(client)
    file_id = response.json()["file_id"]
    row = await attachment_row(file_id)
    assert row is not None
    path = get_storage().path_for(row.storage_key)
    assert path.is_file()

    deleted = await client.delete(f"/api/v1/files/{file_id}")
    assert deleted.status_code == 204
    assert await attachment_row(file_id) is None
    assert await chunk_rows(file_id) == []
    assert not path.exists()
    assert (await client.delete(f"/api/v1/files/{file_id}")).status_code == 404


async def test_upload_rejects_unsupported_extension(client: AsyncClient):
    response = await upload_file(client, name="virus.exe", content=b"MZ....")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_file_type"
    assert await _attachment_count() == 0


async def test_upload_rejects_image_for_now(client: AsyncClient):
    response = await upload_file(client, name="photo.png", content=b"\x89PNG\r\n\x1a\n")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_file_type"
    assert await _attachment_count() == 0


async def test_upload_rejects_pdf_magic_mismatch(client: AsyncClient):
    response = await upload_file(client, name="fake.pdf", content=b"not a pdf")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "invalid_file_content"
    assert await _attachment_count() == 0


async def test_upload_rejects_binary_masquerading_as_text(client: AsyncClient):
    response = await upload_file(client, name="fake.txt", content=b"\x00\x01\x02" * 100)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "invalid_file_content"


async def test_upload_rejects_empty_file(client: AsyncClient):
    response = await upload_file(client, content=b"")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "invalid_file_content"


async def test_upload_rejects_oversize(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    response = await upload_file(client, content=b"x" * (1024 * 1024 + 1))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert await _attachment_count() == 0


async def test_corrupt_docx_is_stored_as_failed(client: AsyncClient, tmp_path):
    path = tmp_path / "broken.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "not xml")
    payload = path.read_bytes()

    response = await upload_file(client, name="broken.docx", content=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["parse_status"] == "failed"
    assert body["supported_for_qa"] is False
    assert body["error_message"]
    assert await chunk_rows(body["file_id"]) == []


async def test_upload_to_other_users_conversation_is_404(client: AsyncClient):
    conversation_id = await create_other_users_conversation()
    response = await upload_file(client, conversation_id=conversation_id)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"
    assert await _attachment_count() == 0


async def test_cannot_read_or_delete_other_users_file(client: AsyncClient):
    file_id = await create_other_users_attachment()
    assert (await client.get(f"/api/v1/files/{file_id}")).status_code == 404
    assert (await client.delete(f"/api/v1/files/{file_id}")).status_code == 404
    assert await attachment_row(file_id) is not None


async def test_duplicate_content_creates_two_rows(client: AsyncClient):
    first = await upload_file(client, content=b"same content")
    second = await upload_file(client, content=b"same content")
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["file_id"] != second.json()["file_id"]
    assert await _attachment_count() == 2


async def test_delete_conversation_cascades_attachment_and_disk_file(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await upload_file(client, conversation_id=conversation["id"])
    file_id = response.json()["file_id"]
    row = await attachment_row(file_id)
    assert row is not None
    path = get_storage().path_for(row.storage_key)
    assert path.is_file()

    deleted = await client.delete(f"/api/v1/conversations/{conversation['id']}")
    assert deleted.status_code == 204
    assert await attachment_row(file_id) is None
    assert await chunk_rows(file_id) == []
    assert not path.exists()
