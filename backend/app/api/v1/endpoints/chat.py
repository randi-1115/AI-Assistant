"""流式聊天与重新生成。

关键顺序：**先把准备阶段跑完，再返回 StreamingResponse**。
prepare_send / prepare_regenerate 会在这里抛出 ConversationNotFound 之类的
异常，此时 HTTP 响应还没开始，全局异常处理能正常返回 JSON 错误信封。
一旦进了 StreamingResponse，就只能靠事件流里的 event: error 报错了。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import StreamingResponse

from app.ai.providers.base import LLMProvider
from app.ai.providers.registry import llm_provider_dep
from app.api.deps import get_current_user_id
from app.api.v1.sse import sse_stream
from app.schemas.chat import ChatStreamRequest
from app.search import SearchService, get_search_service
from app.services.chat_service import ChatService, PreparedTurn

router = APIRouter(prefix="/conversations", tags=["chat"])

# no-cache 防止中间层缓存；X-Accel-Buffering 让 nginx 不要缓冲（否则逐字显示会失效）。
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _streaming_response(
    service: ChatService, prepared: PreparedTurn, request: Request
) -> StreamingResponse:
    return StreamingResponse(
        sse_stream(service.run_turn(prepared, request)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: int,
    payload: ChatStreamRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id),
    provider: LLMProvider = Depends(llm_provider_dep),
    search: SearchService = Depends(get_search_service),
) -> StreamingResponse:
    service = ChatService(provider, search_service=search)
    prepared = await service.prepare_send(
        conversation_id=conversation_id,
        user_id=user_id,
        content=payload.content,
        attachment_ids=payload.attachment_ids,
        search_mode=payload.search_mode.value,
    )

    # 首轮对话结束后自动起标题。用 BackgroundTasks 而不是 asyncio.create_task：
    # asyncio 只持有 Task 的弱引用，裸 create_task 的任务可能在跑完前被 GC 掉。
    # FastAPI 会把 BackgroundTasks 挂到返回的 Response 上，在流结束后执行。
    # 标题生成失败只会降级成截断首句，绝不影响聊天本身。
    if prepared.is_first_turn and prepared.title_is_default:
        background_tasks.add_task(
            service.generate_title, prepared.conversation_id, prepared.first_user_content
        )

    return _streaming_response(service, prepared, request)


@router.post("/{conversation_id}/regenerate")
async def regenerate_message(
    conversation_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    provider: LLMProvider = Depends(llm_provider_dep),
    search: SearchService = Depends(get_search_service),
) -> StreamingResponse:
    service = ChatService(provider, search_service=search)
    prepared = await service.prepare_regenerate(
        conversation_id=conversation_id, user_id=user_id
    )
    return _streaming_response(service, prepared, request)
