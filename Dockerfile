# syntax=docker/dockerfile:1.7

# ── Stage 1: build the viewer SPA (React 19 + Vite 8) ────────
FROM node:22-alpine AS viewer-build
WORKDIR /viewer
COPY viewer-ui/package.json viewer-ui/package-lock.json ./
RUN npm ci
COPY viewer-ui/ ./
RUN npm run build

# ── Stage 2: Python runtime ──────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app
# Built SPA goes into viewer-ui/dist (preferred by app/main.py).
COPY --from=viewer-build /viewer/dist ./viewer-ui/dist

EXPOSE 8765

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
