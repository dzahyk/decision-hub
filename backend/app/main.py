import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings

app = FastAPI(title="Decision Hub API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "Idempotent-Replay"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Every request gets an id that is stored on audit events and echoed back (A-NFR-06)."""
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex
    request.state.request_id = rid[:64]
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


app.include_router(router)
