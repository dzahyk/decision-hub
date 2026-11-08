# Decision Hub — single container: FastAPI serves the API and the built frontend.
# Build:  docker build -t decision-hub .
# Run:    see docker-compose.yml (needs PostgreSQL via DH_DATABASE_URL)

# ---------- frontend build
FROM node:22-alpine AS web
WORKDIR /web
RUN npm install -g pnpm@11.25.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

# ---------- runtime
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.12.7
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY --from=web /web/dist /app/static
ENV PATH="/app/.venv/bin:$PATH" \
    DH_STATIC_DIR=/app/static \
    DH_COOKIE_SECURE=true \
    DH_CORS_ORIGINS='[]'
RUN useradd --system --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/v1/health')"
# Migrations and idempotent demo seed run on start; then serve.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
