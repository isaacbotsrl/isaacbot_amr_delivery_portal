# Isaacbot delivery portal: React UI (built here) served by the FastAPI backend.

FROM node:22.12-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY frontend/ ./
RUN npm run build && npm test

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# `docker build --target test .` runs the backend tests (the default build runs them too).
FROM base AS test
COPY backend/requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY backend/ ./
RUN python -m pytest -q tests && touch /tests-passed

FROM base
COPY --from=test /tests-passed /tests-passed
COPY backend/delivery_portal ./delivery_portal
COPY backend/tools ./tools
COPY --from=ui /ui/dist ./static
ENV STATIC_DIR=/app/static DATABASE=/data/tickets.sqlite
RUN useradd --system --uid 10001 portal && mkdir -p /data && chown portal /data
USER portal
VOLUME /data
EXPOSE 8040
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8040/api/health', timeout=2)"
CMD ["uvicorn", "delivery_portal.presentation.main:app", "--host", "0.0.0.0", "--port", "8040", "--proxy-headers"]
