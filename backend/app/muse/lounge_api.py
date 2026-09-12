"""楽屋と手帖の口。（2026-09-12）

総監督のご判断で Muse Classic を退役させ、Muse Refine を正規の Muse にした。
**楽屋だけは classic のルーターに載っていた** —— 撮影室の30本と同じ
`/api/muse` の下に、`lounge/*` と `handpost` の6本が同居していた。

撮影室のほうは退役するが、楽屋は絶対に削らない（総監督の指示は最初から一貫
している）。だから6本だけここへ移す。画面の側（`CharacterGallery` と
`LoungePanel`）は `/api/muse/lounge/...` を叩いているので、**URL は変えない**。

書き込む側（お出かけ・報告・提案・癖メモ・ケミストリー）は `muse/shared.py`
の `finish_session` が積む。ここは読むだけ。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import handpost_db, lounge as lounge_mod, lounge_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/muse")


def _db(request: Request):
    return request.app.state.db


class LikeBody(BaseModel):
    liked: bool | None = None


async def _faces(request: Request) -> dict[str, str]:
    """`{character_id: 顔の sha}`。**一覧を一度だけ引く。**

    楽屋には何人も出てくるので、一人ずつ preset を引くと件数ぶん往復する。
    """
    from ..characters import presets as presets_db
    out: dict[str, str] = {}
    try:
        for p in await presets_db.list_presets(_db(request)):
            sha = str((p.get("board") or {}).get("portrait") or "")
            if sha:
                out[str(p.get("id") or "")] = sha
    except Exception:
        pass                      # 顔が出ないだけ。楽屋は読めるほうが大事
    return out


@router.get("/lounge/threads")
async def lounge_threads(request: Request, limit: int = 40, kind: str = ""):
    from . import lounge_db
    rows = await lounge_db.list_threads(_db(request), limit=limit, kind=kind)
    lounge_mod.stamp_faces(rows, await _faces(request))
    return {"threads": rows}
@router.get("/lounge/threads/{thread_id}")
async def lounge_thread(thread_id: str, request: Request):
    from . import lounge_db
    row = await lounge_db.get_thread(_db(request), thread_id)
    if row is None:
        raise HTTPException(404, "thread not found")
    lounge_mod.stamp_faces([row], await _faces(request))
    return row
@router.get("/lounge/trends")
async def lounge_trends(request: Request):
    from . import lounge_db
    return {"trends": await lounge_db.get_trends(_db(request))}
@router.post("/lounge/threads/{thread_id}/like")
async def lounge_like(thread_id: str, request: Request, body: LikeBody = LikeBody()):
    """Toggle or set liked on a pitch (or any lounge thread)."""
    from . import lounge_db
    liked = body.liked
    row = await lounge_db.set_thread_liked(_db(request), thread_id, liked)
    if row is None:
        raise HTTPException(404, "thread not found")
    return row
@router.get("/lounge/summary")
async def lounge_summary(request: Request, since: float = 0.0):
    """Gallery badge: new threads since last peek + unanswered pitches."""
    from . import lounge_db
    return await lounge_db.summary(_db(request), since=since)
@router.get("/handpost")
async def handpost_list(request: Request, pinned_only: bool = False):
    """Read-only list. Pages are written by habit jobs — not by the showrunner."""
    from . import handpost_db
    pages = await handpost_db.list_pages(_db(request), pinned_only=pinned_only)
    # **既に保存された頁も、ここで切る。** 書く側は直したが、壊れたまま
    # 残っている頁は新しいものが来るまで表示され続ける（実測 4頁中2頁）。
    # 保存し直しはしない —— 読むたびに整えるだけで足りる。
    for page in pages:
        if not isinstance(page, dict):
            continue
        ja, spilled = lounge_mod.split_trailing_english(page.get("body_ja") or "")
        if spilled:
            page["body_ja"] = ja
            if not str(page.get("body_en") or "").strip() or                     "English" in str(page.get("body_en") or ""):
                page["body_en"] = spilled
    return {"pages": pages}
