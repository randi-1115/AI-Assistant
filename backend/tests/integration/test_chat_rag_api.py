"""文件问答：附件检索、来源事件、失败附件与重新生成。"""

from __future__ import annotations

import zipfile

from httpx import AsyncClient

from tests.integration.conftest import (
    create_conversation,
    create_other_users_attachment,
    upload_file,
)
from tests.integration.test_chat_stream_api import names, parse_sse, payload


async def stream_with_attachments(
    client: AsyncClient,
    conversation_id: int,
    content: str,
    attachment_ids: list[int],
):
    return await client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": content, "attachment_ids": attachment_ids},
    )


async def messages_of(client: AsyncClient, conversation_id: int) -> list[dict]:
    response = await client.get(f"/api/v1/conversations/{conversation_id}/messages")
    assert response.status_code == 200
    return response.json()


def role_message(messages: list[dict], role: str) -> dict:
    return next(message for message in messages if message["role"] == role)


async def test_chat_with_attachment_emits_sources_and_persists_metadata(
    client: AsyncClient,
):
    uploaded = await upload_file(
        client,
        name="项目说明.txt",
        content="项目代号是蓝鲸。\n\n部署窗口安排在周五晚上。".encode(),
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["file_id"]
    conversation = await create_conversation(client)

    response = await stream_with_attachments(
        client, conversation["id"], "项目代号是什么？", [file_id]
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    order = names(events)

    assert order[0] == "message_start"
    assert "source" in order
    assert order.index("source") < order.index("token") < order.index("message_end")

    source = payload(events, "source")
    assert source["kind"] == "file"
    assert source["file_id"] == file_id
    assert source["title"] == "项目说明.txt"
    assert source["url"] is None
    assert source["chunk_index"] == 0
    assert source["snippet"]
    assert source["score"] > 0

    messages = await messages_of(client, conversation["id"])
    user = role_message(messages, "user")
    assistant = role_message(messages, "assistant")
    assert user["metadata_json"]["attachments"][0]["id"] == file_id
    assert assistant["metadata_json"]["sources"][0]["file_id"] == file_id


async def test_generic_summary_query_uses_attachment_baseline(
    client: AsyncClient, tmp_path
):
    from docx import Document

    path = tmp_path / "chapter.docx"
    document = Document()
    document.add_paragraph("神经网络由输入层、隐藏层和输出层组成。")
    document.add_paragraph("反向传播通过链式法则计算梯度并更新权重。")
    document.save(path)

    uploaded = await upload_file(
        client, name="chapter.docx", content=path.read_bytes()
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["parse_status"] == "ready"
    file_id = body["file_id"]
    conversation = await create_conversation(client)

    response = await stream_with_attachments(
        client, conversation["id"], "总结这个文件", [file_id]
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)

    sources = [data for name, data in events if name == "source"]
    assert sources, "总结类问题应回退到文档基线片段，而不是返回空"
    assert sources[0]["kind"] == "file"

    assistant = role_message(await messages_of(client, conversation["id"]), "assistant")
    saved = assistant["metadata_json"]["sources"]
    assert saved and saved[0]["kind"] == "file"


async def test_heading_only_docx_is_retrievable(client: AsyncClient, tmp_path):
    from docx import Document

    path = tmp_path / "headings.docx"
    document = Document()
    document.add_heading("第五章 神经网络", level=1)
    document.add_heading("反向传播", level=2)
    document.save(path)

    uploaded = await upload_file(
        client, name="headings.docx", content=path.read_bytes()
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["parse_status"] == "ready"
    conversation = await create_conversation(client)

    response = await stream_with_attachments(
        client, conversation["id"], "反向传播是什么", [body["file_id"]]
    )
    events = parse_sse(response.text)
    sources = [data for name, data in events if name == "source"]

    assert sources
    assert sources[0]["kind"] == "file"
    assert "反向传播" in (sources[0]["snippet"] or "")


async def test_table_docx_question_hits_table_chunk(client: AsyncClient, tmp_path):
    from docx import Document

    path = tmp_path / "table.docx"
    document = Document()
    document.add_heading("实验设置", level=1)
    document.add_paragraph("超参数如下。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "名称"
    table.cell(0, 1).text = "值"
    table.cell(1, 0).text = "学习率"
    table.cell(1, 1).text = "0.01"
    document.save(path)

    uploaded = await upload_file(client, name="table.docx", content=path.read_bytes())
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["parse_status"] == "ready"
    conversation = await create_conversation(client)

    response = await stream_with_attachments(
        client, conversation["id"], "学习率是多少", [body["file_id"]]
    )
    events = parse_sse(response.text)
    sources = [data for name, data in events if name == "source"]

    assert sources
    assert sources[0]["kind"] == "file"
    assert "学习率" in (sources[0]["snippet"] or "")


async def test_chat_without_matching_text_emits_no_sources(client: AsyncClient):
    uploaded = await upload_file(client, content="项目代号是蓝鲸。".encode())
    file_id = uploaded.json()["file_id"]
    conversation = await create_conversation(client)

    response = await stream_with_attachments(
        client, conversation["id"], "量子力学的测不准原理是什么？", [file_id]
    )
    assert response.status_code == 200
    assert "source" not in names(parse_sse(response.text))

    assistant = role_message(await messages_of(client, conversation["id"]), "assistant")
    assert not (assistant["metadata_json"] or {}).get("sources")


async def test_failed_attachment_does_not_block_chat(client: AsyncClient, tmp_path):
    path = tmp_path / "broken.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "not xml")
    uploaded = await upload_file(client, name="broken.docx", content=path.read_bytes())
    body = uploaded.json()
    assert body["parse_status"] == "failed"

    conversation = await create_conversation(client)
    response = await stream_with_attachments(client, conversation["id"], "总结一下", [body["file_id"]])
    assert response.status_code == 200
    assert "source" not in names(parse_sse(response.text))

    user = role_message(await messages_of(client, conversation["id"]), "user")
    snapshot = user["metadata_json"]["attachments"][0]
    assert snapshot["parse_status"] == "failed"
    assert snapshot["supported_for_qa"] is False


async def test_foreign_attachment_is_404_before_messages_are_stored(
    client: AsyncClient,
):
    file_id = await create_other_users_attachment()
    conversation = await create_conversation(client)

    response = await stream_with_attachments(
        client, conversation["id"], "看看这个文件", [file_id]
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"
    assert await messages_of(client, conversation["id"]) == []


async def test_too_many_attachment_ids_rejected(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await stream_with_attachments(
        client, conversation["id"], "问题", list(range(1, 12))
    )
    assert response.status_code == 422


async def test_duplicate_attachment_ids_are_deduped(client: AsyncClient):
    uploaded = await upload_file(client, content="项目代号是蓝鲸。".encode())
    file_id = uploaded.json()["file_id"]
    conversation = await create_conversation(client)

    response = await stream_with_attachments(
        client, conversation["id"], "项目代号是什么", [file_id, file_id]
    )
    assert response.status_code == 200
    user = role_message(await messages_of(client, conversation["id"]), "user")
    assert len(user["metadata_json"]["attachments"]) == 1


async def test_regenerate_reuses_attachment_snapshot(client: AsyncClient):
    uploaded = await upload_file(client, content="项目代号是蓝鲸。".encode())
    file_id = uploaded.json()["file_id"]
    conversation = await create_conversation(client)
    first = await stream_with_attachments(
        client, conversation["id"], "项目代号是什么", [file_id]
    )
    assert "source" in names(parse_sse(first.text))

    regenerated = await client.post(f"/api/v1/conversations/{conversation['id']}/regenerate")
    assert regenerated.status_code == 200, regenerated.text
    events = parse_sse(regenerated.text)
    assert "source" in names(events)
    assert payload(events, "source")["file_id"] == file_id

    assistant = role_message(await messages_of(client, conversation["id"]), "assistant")
    assert assistant["metadata_json"]["sources"][0]["file_id"] == file_id


async def test_regenerate_after_attachment_deleted_still_works(client: AsyncClient):
    uploaded = await upload_file(client, content="项目代号是蓝鲸。".encode())
    file_id = uploaded.json()["file_id"]
    conversation = await create_conversation(client)
    await stream_with_attachments(client, conversation["id"], "项目代号是什么", [file_id])

    assert (await client.delete(f"/api/v1/files/{file_id}")).status_code == 204

    regenerated = await client.post(f"/api/v1/conversations/{conversation['id']}/regenerate")
    assert regenerated.status_code == 200, regenerated.text
    assert "source" not in names(parse_sse(regenerated.text))
