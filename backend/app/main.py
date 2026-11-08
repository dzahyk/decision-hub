import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings

app = FastAPI(title="Decision Hub API", version="0.5.0")
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "Idempotent-Replay"],
)

# Strict by default; the proposal endpoint sets its own (stricter) policy.
APP_CSP = (
    "default-src 'self'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


@app.middleware("http")
async def request_id_and_headers(request: Request, call_next):
    """Request id on every response and audit event (A-NFR-06) + baseline security headers."""
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex
    request.state.request_id = rid[:64]
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", APP_CSP)
    if request.url.path.startswith("/v1/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path.startswith("/assets/") and response.status_code == 200:
        # Vite file names carry a content hash, so they never change in place.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


app.include_router(router)

# Serve the built frontend (single deployable unit). Absent in local dev, where Vite serves it.
STATIC = Path(settings.static_dir).resolve() if settings.static_dir else None
if STATIC and (STATIC / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("v1/"):
            return JSONResponse({"detail": {"code": "not_found", "message": "not found"}}, 404)
        file = (STATIC / path).resolve()
        if path and file.is_file() and STATIC in file.parents:  # no path traversal
            return FileResponse(file)
        if "." in path.rsplit("/", 1)[-1]:  # a missing *file* is a 404, not the app shell
            return JSONResponse({"detail": {"code": "not_found", "message": "not found"}}, 404)
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
