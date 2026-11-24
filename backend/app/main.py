"""FastAPI application entrypoint."""
from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import accounts, alerts, payouts, rules, graph, scoring, users, reports
from app.db import init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="YouTrade Antifraud API",
    version="1.0.0",
    description="Anti-fraud system for prop trading platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8000",
        "https://antifrod.youtradeprop.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
app.include_router(accounts.router, prefix="/api/v1/accounts", tags=["Accounts"])
app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
app.include_router(payouts.router, prefix="/api/v1/payouts", tags=["Payouts"])
app.include_router(rules.router, prefix="/api/v1/rules", tags=["Rules"])
app.include_router(graph.router, prefix="/api/v1/graph", tags=["Graph"])
app.include_router(scoring.router, prefix="/api/v1/scoring", tags=["Risk Scoring"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["Reports"])


@app.get("/health")
async def health():
    return {"status": "ok"}


# Serve SPA frontend — must be mounted LAST (catches all unmatched paths)
_frontend_dir = Path("/app/frontend")
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
