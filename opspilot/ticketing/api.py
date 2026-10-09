"""Standalone mock ticketing REST API (SQLite + FastAPI).

Mounted inside the main app at /ticketing, or run on its own:
    uvicorn opspilot.ticketing.api:create_standalone_app --factory --port 8100
Point OPSPILOT_TICKET_API_URL at it and the tools talk to it over HTTP instead of in-process.
"""

from __future__ import annotations

import hmac
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from opspilot.config import Settings, get_settings
from opspilot.db import Database
from opspilot.ticketing.service import LocalTicketBackend


class TicketCreate(BaseModel):
    requester_id: str
    title: str = Field(min_length=3, max_length=140)
    description: str = ""
    category: str = "general"
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    source: str = "api"


class CommentCreate(BaseModel):
    author: str
    body: str = Field(min_length=1, max_length=2000)


class StatusUpdate(BaseModel):
    status: Literal["open", "in_progress", "waiting_on_user", "resolved", "closed"]


def build_router(backend: LocalTicketBackend, token: str) -> APIRouter:
    router = APIRouter(prefix="/ticketing/v1", tags=["ticketing (mock)"])

    def auth(authorization: str | None = Header(default=None)) -> None:
        if not token:  # open in dev only; the container refuses to start without a token in prod
            return
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, token):
            raise HTTPException(401, "invalid service token")

    @router.post("/tickets", dependencies=[Depends(auth)], status_code=201)
    def create(body: TicketCreate) -> dict[str, Any]:
        return backend.create(**body.model_dump())

    @router.get("/tickets", dependencies=[Depends(auth)])
    def list_(requester_id: str | None = None, status: str | None = None, limit: int = Query(50, le=200)):
        return backend.list_for(requester_id, status, limit)

    @router.get("/tickets/{key}", dependencies=[Depends(auth)])
    def get(key: str):
        t = backend.get(key)
        if not t:
            raise HTTPException(404, "ticket not found")
        return t

    @router.post("/tickets/{key}/comments", dependencies=[Depends(auth)])
    def comment(key: str, body: CommentCreate):
        t = backend.add_comment(key, body.author, body.body)
        if not t:
            raise HTTPException(404, "ticket not found")
        return t

    @router.patch("/tickets/{key}", dependencies=[Depends(auth)])
    def update(key: str, body: StatusUpdate):
        if not backend.get(key):
            raise HTTPException(404, "ticket not found")
        return backend.set_status(key, body.status)

    return router


def create_standalone_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.resolved_db_path)
    app = FastAPI(title="OpsPilot mock ticketing API")
    app.include_router(build_router(LocalTicketBackend(db), settings.ticket_api_token))
    return app
