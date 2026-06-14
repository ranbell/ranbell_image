from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SPIRIT_ORDER = ["faithful", "rebel", "stranger", "lunatic", "oracle"]
SESSION_TTL = 3600  # seconds


@dataclass
class SpiritState:
    name: str
    status: str = "waiting"   # waiting|composing|generating|tagging|scoring|done|error
    sha256: str | None = None
    prompt_result: dict | None = None
    alignment_score: float | None = None
    job_ids: list[str] = field(default_factory=list)


@dataclass
class InvokeSession:
    session_id: str
    user_intent: str
    input_mode: str
    workflow_name: str
    enabled_spirits: list[str]
    prompt_mode: str = "danbooru+natural"  # 'danbooru+natural' | 'natural' | 'danbooru'
    locale: str = "en"                     # 'en' | 'ja' — controls monologue language
    person_tags: str = ""                  # e.g. "1girl, solo" — prepended to every positive prompt
    pro_negative: str = ""                 # user-supplied negative from Pro mode
    # Runtime resources (stored to avoid threading through callbacks)
    db: Any = None
    ollama: Any = None
    comfy: Any = None
    spooler: Any = None
    spirits: dict[str, SpiritState] = field(default_factory=dict)
    axes: dict | None = None
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    created_at: float = field(default_factory=time.time)
    completed: bool = False
    cancelled: bool = False

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_intent": self.user_intent,
            "input_mode": self.input_mode,
            "workflow_name": self.workflow_name,
            "enabled_spirits": self.enabled_spirits,
            "axes": {k: v for k, v in (self.axes or {}).items() if not k.startswith("_")},
            "spirits": {
                name: {
                    "status": s.status,
                    "sha256": s.sha256,
                    "alignment_score": s.alignment_score,
                    "monologue": (s.prompt_result or {}).get("internal_monologue"),
                    "natural_language": (s.prompt_result or {}).get("natural_language"),
                    "natural_language_ja": (s.prompt_result or {}).get("natural_language_ja"),
                    "danbooru_tags": (s.prompt_result or {}).get("danbooru_tags"),
                    "wild_tags_used": (s.prompt_result or {}).get("wild_tags_used", []),
                    "inverted_axis": (s.prompt_result or {}).get("inverted_axis"),
                }
                for name, s in self.spirits.items()
            },
        }


class InvokeSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, InvokeSession] = {}

    def create_session(
        self,
        user_intent: str,
        input_mode: str,
        workflow_name: str,
        enabled_spirits: list[str],
        prompt_mode: str = "danbooru+natural",
        locale: str = "en",
        person_tags: str = "",
        pro_negative: str = "",
        db=None,
        ollama=None,
        comfy=None,
        spooler=None,
    ) -> InvokeSession:
        session_id = str(uuid.uuid4())
        enabled = [s for s in SPIRIT_ORDER if s in enabled_spirits] or SPIRIT_ORDER
        session = InvokeSession(
            session_id=session_id,
            user_intent=user_intent,
            input_mode=input_mode,
            workflow_name=workflow_name,
            enabled_spirits=enabled,
            prompt_mode=prompt_mode,
            locale=locale,
            person_tags=person_tags,
            pro_negative=pro_negative,
            db=db,
            ollama=ollama,
            comfy=comfy,
            spooler=spooler,
            spirits={name: SpiritState(name=name) for name in enabled},
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> InvokeSession | None:
        self._evict_expired()
        return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _evict_expired(self) -> None:
        cutoff = time.time() - SESSION_TTL
        expired = [sid for sid, s in self._sessions.items() if s.created_at < cutoff]
        for sid in expired:
            self._sessions.pop(sid, None)

    async def emit(self, session: InvokeSession, event_type: str, data: dict) -> None:
        await session.event_queue.put({"type": event_type, **data})

    async def on_axis_done(self, session_id: str, axes: dict) -> None:
        session = self.get_session(session_id)
        if not session:
            return
        session.axes = axes

        await self.emit(session, "axis_done", {"axes": axes})

        # Extract axis tags for vocab lookup
        axis_tags = []
        for v in axes.values():
            if isinstance(v, list):
                axis_tags.extend(v)
            elif isinstance(v, str) and v:
                axis_tags.extend(v.replace(",", " ").split())

        from .vocab_bank import get_vocab_hints
        try:
            vocab_hints = await get_vocab_hints(session.db, session.ollama, axis_tags)
        except Exception as e:
            logger.warning("vocab_hints failed: %s", e)
            vocab_hints = {"stranger": [], "lunatic": []}

        from ..spooler.models import JobLane
        from ..jobs.runners import run_invoke_spirit_compose

        for spirit_name in session.enabled_spirits:
            session.spirits[spirit_name].status = "composing"
            spirit_vocab = vocab_hints if spirit_name in ("stranger", "lunatic") else {"stranger": [], "lunatic": []}

            job_id = session.spooler.submit(
                JobLane.PROMPT,
                f"invoke.spirit/{spirit_name[:3]}",
                run_invoke_spirit_compose,
                meta={"session_id": session_id, "spirit": spirit_name},
                session_id=session_id,
                spirit_name=spirit_name,
                axes=axes,
                vocab_hints=spirit_vocab,
                locale=session.locale,
                session_manager=self,
            )
            session.spirits[spirit_name].job_ids.append(job_id)

    async def on_spirit_composed(
        self,
        session_id: str,
        spirit_name: str,
        prompt_result: dict,
    ) -> None:
        session = self.get_session(session_id)
        if not session:
            return
        spirit = session.spirits.get(spirit_name)
        if not spirit:
            return

        spirit.prompt_result = prompt_result
        spirit.status = "generating"

        await self.emit(session, "spirit_composed", {
            "spirit": spirit_name,
            "monologue": prompt_result.get("internal_monologue", ""),
            "natural_language": prompt_result.get("natural_language", ""),
            "natural_language_ja": prompt_result.get("natural_language_ja", ""),
        })

        from ..spooler.models import JobLane
        from ..jobs.runners import run_invoke_image_generate

        job_id = session.spooler.submit(
            JobLane.GENERATION,
            f"invoke.gen/{spirit_name[:3]}",
            run_invoke_image_generate,
            meta={"session_id": session_id, "spirit": spirit_name},
            session_id=session_id,
            spirit_name=spirit_name,
            prompt_result=prompt_result,
            workflow_name=session.workflow_name,
            seed=None,
            session_manager=self,
        )
        spirit.job_ids.append(job_id)

    async def on_image_done(
        self,
        session_id: str,
        spirit_name: str,
        sha256: str,
    ) -> None:
        session = self.get_session(session_id)
        if not session:
            return
        spirit = session.spirits.get(spirit_name)
        if not spirit:
            return

        spirit.sha256 = sha256
        spirit.status = "tagging"

        genesis = _build_genesis(session, spirit_name, sha256)
        try:
            await session.db.set_genesis_payload(sha256, genesis)
        except Exception as e:
            logger.warning("genesis payload write failed for %s: %s", sha256, e)

        await self.emit(session, "image_ready", {
            "spirit": spirit_name,
            "sha256": sha256,
        })

        from ..spooler.models import JobLane
        from ..jobs.runners import run_invoke_alignment_score

        job_eval = session.spooler.submit(
            JobLane.EVALUATION,
            f"invoke.align/{spirit_name[:3]}",
            run_invoke_alignment_score,
            meta={"session_id": session_id, "spirit": spirit_name},
            sha256=sha256,
            session_id=session_id,
            spirit_name=spirit_name,
            session_manager=self,
            db=session.db,
            ollama=session.ollama,
        )
        spirit.job_ids.append(job_eval)

    async def on_spirit_done(
        self,
        session_id: str,
        spirit_name: str,
        alignment_score: float | None,
    ) -> None:
        session = self.get_session(session_id)
        if not session:
            return
        spirit = session.spirits.get(spirit_name)
        if not spirit:
            return

        spirit.alignment_score = alignment_score
        spirit.status = "done"

        await self.emit(session, "spirit_done", {
            "spirit": spirit_name,
            "sha256": spirit.sha256,
            "alignment_score": alignment_score,
        })

        if all(session.spirits[n].status in ("done", "error") for n in session.enabled_spirits):
            session.completed = True
            await self.emit(session, "session_complete", {"session_id": session_id})
            await _update_summon_stats(session=session)
            await session.event_queue.put(None)
            _submit_pack_pipeline(session, session_id)

    async def on_spirit_error(self, session_id: str, spirit_name: str, error: str) -> None:
        session = self.get_session(session_id)
        if not session:
            return
        spirit = session.spirits.get(spirit_name)
        if spirit:
            spirit.status = "error"
        await self.emit(session, "spirit_error", {"spirit": spirit_name, "error": error})

        if all(session.spirits[n].status in ("done", "error") for n in session.enabled_spirits):
            session.completed = True
            await self.emit(session, "session_complete", {"session_id": session_id})
            await session.event_queue.put(None)
            _submit_pack_pipeline(session, session_id)

    async def cancel_session(self, session_id: str) -> bool:
        session = self.get_session(session_id)
        if not session or session.completed:
            return False
        session.cancelled = True
        all_job_ids = [jid for s in session.spirits.values() for jid in s.job_ids]
        for job_id in all_job_ids:
            try:
                await session.spooler.cancel(job_id)
            except Exception:
                pass
        for spirit in session.spirits.values():
            if spirit.status not in ("done", "error"):
                spirit.status = "error"
        session.completed = True
        await self.emit(session, "session_cancelled", {"session_id": session_id})
        await session.event_queue.put(None)
        return True

    async def adopt_spirit(self, session_id: str, spirit_name: str) -> str | None:
        session = self.get_session(session_id)
        if not session:
            return None
        spirit = session.spirits.get(spirit_name)
        if not spirit or not spirit.sha256:
            return None

        siblings = [
            session.spirits[n].sha256
            for n in session.enabled_spirits
            if n != spirit_name and session.spirits[n].sha256
        ]
        genesis = _build_genesis(session, spirit_name, spirit.sha256, adopted=True, siblings=siblings)
        try:
            await session.db.set_genesis_payload(spirit.sha256, genesis)
        except Exception as e:
            logger.warning("adopt genesis update failed: %s", e)

        try:
            cfg = await session.db.get_config()
            stats = cfg.get("invoke_stats_v1") or {}
            adoption = stats.get("spirit_adoption", {})
            adoption[spirit_name] = adoption.get(spirit_name, 0) + 1
            stats["spirit_adoption"] = adoption
            await session.db.put_config({"invoke_stats_v1": stats})
        except Exception as e:
            logger.warning("adopt stats update failed: %s", e)

        return spirit.sha256


def _submit_pack_pipeline(session: InvokeSession, session_id: str) -> None:
    """Submit a run_pipeline job at session end to process all pending invoke images (idempotent)."""
    from ..jobs.runners import run_pipeline
    from ..spooler.models import JobLane
    session.spooler.submit(
        JobLane.EMBEDDING,
        f"invoke.pack/{session_id[:8]}",
        run_pipeline,
        db=session.db,
        ollama=session.ollama,
    )


def _build_genesis(
    session: InvokeSession,
    spirit_name: str,
    sha256: str,
    adopted: bool = False,
    siblings: list[str] | None = None,
) -> dict:
    pr = session.spirits[spirit_name].prompt_result or {}
    daily_date = (session.axes or {}).get("_daily_oracle_date") if session.input_mode == "daily_oracle" else None
    return {
        "spirit": spirit_name,
        "session_id": session.session_id,
        "original_intent": session.user_intent,
        "input_mode": session.input_mode,
        "axes_snapshot": {k: v for k, v in (session.axes or {}).items() if not k.startswith("_")},
        "siblings": siblings or [],
        "adopted_at_genesis": adopted,
        "alignment_at_genesis": session.spirits[spirit_name].alignment_score,
        "wild_tags": pr.get("wild_tags_used", []),
        "respin_count": 0,
        "workflow_preset": session.workflow_name,
        "daily_oracle_date": daily_date,
    }


async def _update_summon_stats(session: InvokeSession) -> None:
    from datetime import datetime
    try:
        cfg = await session.db.get_config()
        stats: dict = cfg.get("invoke_stats_v1") or {}
        month_key = datetime.now().strftime("%Y-%m")
        by_month = stats.get("summoned_by_month", {})
        by_month[month_key] = by_month.get(month_key, 0) + 1
        stats["summoned_by_month"] = by_month
        stats["summoned_total"] = sum(by_month.values())
        lunatic = session.spirits.get("lunatic")
        if lunatic and lunatic.sha256:
            stats["lunatic_total"] = stats.get("lunatic_total", 0) + 1
        await session.db.put_config({"invoke_stats_v1": stats})
    except Exception as e:
        logger.warning("summon stats update failed: %s", e)
