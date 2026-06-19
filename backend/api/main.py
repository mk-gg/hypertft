"""
api/main.py
FastAPI application entry point.

Run locally:
    uvicorn api.main:app --reload --port 8000

Production (e.g. Railway / Render):
    uvicorn api.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import APIConfig
from api.dependencies import get_cache, get_pool
from api.routers import comp, meta
from shared.db import init_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

config = APIConfig()

app = FastAPI(
    title="HyperTFT API",
    description="Aggregated TFT match stats for Platinum+ players.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — restrict to your domain in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Routers
app.include_router(meta.router)
app.include_router(comp.router)


@app.on_event("startup")
def on_startup() -> None:
    """Open the connection pool and cache, and ensure the schema exists."""
    init_schema(get_pool())
    get_cache()  # establishes the Redis connection (or logs that it is disabled)
    logger.info("HyperTFT API started.")


@app.on_event("shutdown")
def on_shutdown() -> None:
    """Close the connection pool and cache cleanly on shutdown."""
    get_pool().close()
    get_cache().close()
    logger.info("HyperTFT API stopped.")


@app.get("/health", tags=["health"])
def health_check():
    """Simple liveness probe for load balancers / uptime monitors."""
    return {"status": "ok"}
