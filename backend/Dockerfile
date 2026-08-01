# syntax=docker/dockerfile:1
# uv 공식 이미지를 쓰면 Python과 uv가 함께 고정된다.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# 의존성 레이어를 소스와 분리해 캐시 적중률을 높인다.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime

# 루트로 돌리지 않는다. data 볼륨은 이 사용자가 쓸 수 있어야 한다.
RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app app ./app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

RUN mkdir -p /app/data && chown app:app /app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
