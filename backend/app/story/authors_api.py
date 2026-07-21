"""Author archetype registry API (no personal names)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import settings
from . import authors as authors_db

router = APIRouter(prefix="/api/authors")


class AuthorCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    style_description: str = Field(..., min_length=1)
    genre_tag: str = ""


class AuthorUpdate(BaseModel):
    name: str | None = None
    style_description: str | None = None
    genre_tag: str | None = None


@router.get("")
async def list_authors(request: Request):
    rows = await authors_db.list_authors(request.app.state.db)
    return {"authors": rows}


@router.post("")
async def create_author(body: AuthorCreate, request: Request):
    try:
        row = await authors_db.create_author(
            request.app.state.db,
            name=body.name,
            style_description=body.style_description,
            genre_tag=body.genre_tag,
            vector_dim=settings.embed_dim,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return row


@router.put("/{author_id}")
async def update_author(author_id: str, body: AuthorUpdate, request: Request):
    try:
        row = await authors_db.update_author(
            request.app.state.db,
            author_id,
            name=body.name,
            style_description=body.style_description,
            genre_tag=body.genre_tag,
        )
    except KeyError:
        raise HTTPException(404, "author not found") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return row


@router.delete("/{author_id}")
async def delete_author(author_id: str, request: Request):
    await authors_db.delete_author(request.app.state.db, author_id)
    return {"ok": True}
