"""FastAPI entrypoint placeholder. Business routes are not implemented yet."""
from fastapi import FastAPI

app = FastAPI(title="AI Assistant API", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "note": "skeleton only; database check is not wired yet"}
