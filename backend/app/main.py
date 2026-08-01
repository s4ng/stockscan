"""FastAPI 엔트리포인트."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.nodes  # noqa: F401  — 임포트 부수효과로 노드가 레지스트리에 등록된다
from app.api.routes import router
from app.core.config import get_settings
from app.storage.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="tradeflow",
        description="노드 기반 비주얼 파이프라인 트레이딩 신호 시스템",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router)
    return application


app = create_app()
