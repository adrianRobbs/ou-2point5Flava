# Two stages because the web service needs both toolchains and Render's
# native runtimes are single-language: Node builds the frontend, then a
# plain Python image runs the backend. See render.yaml — the two cron
# services need none of this, they stay on the native python runtime.

FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
WORKDIR /app

# uv, matching how the project is developed and tested everywhere else in
# this repo — not pip, so the lockfile (uv.lock) is the actual source of
# resolved versions here too, not re-resolved at build time.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY db ./db
RUN uv sync --frozen --no-dev

COPY --from=frontend-build /app/frontend/dist ./frontend/dist

ENV PATH="/app/.venv/bin:$PATH"
# Absolute and explicit, not left to __file__-relative arithmetic (see
# webapp/main.py) — whether that would resolve correctly here depends on
# uv's editable-vs-wheel install mode, which is not a thing worth trusting
# implicitly for something a deploy silently breaking on would be expensive.
ENV FRONTEND_DIST_DIR=/app/frontend/dist

EXPOSE 8000
CMD ["uvicorn", "ou25_pipeline.webapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
