"""Muse orchestration: one function per pipeline step.

Each step reads the session, does its work, writes the session back, and
publishes an SSE event. Steps are separately callable on purpose — the whole
value of the pipeline is being able to look at an intermediate result and run
that step again with different settings, which is also why nothing here caches.
"""
from __future__ import annotations

import logging
import random
from typing import Any

from ..characters import presets as presets_db
from ..prompt.tag_merge import removal_tag_set
from ..runtime_config import get_runtime_config
from ..scanner.drafts import PLAYGROUND_SUBDIR
from ..spooler.models import JobLane
from . import (
    camera, cleanup, compose, harvest, merge, scene, session_db, topup, tracks,
)
from . import slots as slot_defs
from .schema import TRACKS, new_session, public_view

logger = logging.getLogger(__name__)


class MuseError(Exception):
    """A step could not run. The message goes straight to the user."""


def _inputs(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("inputs") or {}


async def create_session(db, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    session = new_session(inputs)
    await session_db.save(db, session)
    return session


async def patch_inputs(db, session: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    session["inputs"] = {**_inputs(session), **{k: v for k, v in patch.items() if v is not None}}
    await session_db.save(db, session)
    return session


async def pick_character(db, session: dict[str, Any], character_id: str) -> dict[str, Any]:
    preset = await presets_db.get_preset(db, character_id)
    if preset is None:
        raise MuseError("character not found")
    # Frozen at pick time: if the registry entry is edited later, a run in
    # progress keeps rendering the character it started with.
    session["character"] = {
        **presets_db.preset_to_character(preset),
        "character_id": character_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    session["inputs"] = {**_inputs(session), "character_id": character_id}
    session_db.log(session, "character", session["character"].get("name", ""))
    await session_db.save(db, session)
    return session


# ── S1 compose ──────────────────────────────────────────────────────────────
async def run_compose(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    """Fill the prompt's slots: model writes, vocabulary tops up, caps apply."""
    inputs = _inputs(session)
    theme = str(inputs.get("theme") or "").strip()
    if not theme:
        raise MuseError("theme is required")
    model = str(inputs.get("light_model") or "").strip()
    if not model:
        raise MuseError("light_model is required")

    cfg = await get_runtime_config(db)
    character = session.get("character") or {}
    filled = await compose.compose_slots(
        theme, character, ollama,
        model=model,
        num_ctx=cfg.get("ollama_num_ctx"),
        db=db,
        supplement=bool(inputs.get("vocab_supplement", True)),
    )
    filled.update(compose.locked_slots(character))

    removal = removal_tag_set(cfg)
    blocked = {t.strip().lower().replace(" ", "_")
               for t in (session.get("rejected_tags") or []) if str(t).strip()}
    blocked |= {str(t).lower() for t in removal}
    session["slots"] = {
        key: [r for r in rows if r["tag"].lower() not in blocked]
        for key, rows in filled.items()
    }
    session["seed_tags"] = _tracks_from_slots(session["slots"])
    if not any(session["seed_tags"].values()):
        _warn(session, "the model returned no usable tags — try another light model")

    session["board"] = {t: [] for t in TRACKS}
    session["harvest"] = {}
    session["topup"] = []
    session["topup_candidates"] = []
    session["merged"] = {}
    session_db.log(
        session, "compose",
        " / ".join(f"{k}:{len(v)}" for k, v in session["slots"].items() if v),
    )
    await session_db.save(db, session)
    return session


def _tracks_from_slots(filled: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Flatten the slots into the two board prompts."""
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in TRACKS}
    for slot in slot_defs.SLOTS:
        if slot.track not in TRACKS:
            continue
        out[slot.track].extend(filled.get(slot.key) or [])
    return out


def user_slots(session: dict[str, Any]) -> dict[str, list[str]]:
    """Style / Shot / Effect — the aspects the user owns."""
    inputs = _inputs(session)
    return {
        "style": _as_tags(inputs.get("style")),
        "effect": _as_tags(inputs.get("effect")),
        "shot": camera.tags_for(
            str(inputs.get("shot") or "auto"), str(inputs.get("angle") or "auto"),
        ),
    }


def _as_tags(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [t.strip() for t in str(value or "").split(",") if t.strip()]


def track_prompt(session: dict[str, Any], track: str) -> tuple[str, str]:
    """The positive/negative a board render for this track uses."""
    negative = str(_inputs(session).get("negative_prompt") or "")
    # The board gets the same labelled shape the final prompt has. A flat list
    # lets one aspect dominate by repetition; the labels keep each in its lane.
    filled = {
        slot.key: [r["tag"] for r in (session.get("slots") or {}).get(slot.key) or []]
        for slot in slot_defs.slots_for(track)
    }
    filled.update({k: v for k, v in user_slots(session).items() if v})
    if track == "person":
        identity = list((session.get("character") or {}).get("identity_tags") or [])
        if identity:
            filled["character"] = identity
    else:
        # Removing person tags from the positive is not enough to keep people
        # out of a background: the checkpoint puts a figure in a library because
        # libraries have figures in them. Say so in the negative.
        away = tracks.BACKGROUND_NEGATIVE
        negative = f"{negative}, {away}" if negative else away
        # And do not ask for one in the positive at the same time. The user's
        # Effect line is written for the finished picture, so it says things
        # like "detailed character" — which on a background board contradicts
        # the negative word for word. Drop only the parts naming what the
        # negative already forbids; the rest of their setting stands.
        for key in ("style", "effect"):
            filled[key] = [t for t in (filled.get(key) or []) if not _names_a_person(t)]
    return slot_defs.render_prompt(filled), negative


_PERSON_WORDS = frozenset(
    w.strip().replace("_", " ") for w in tracks.BACKGROUND_NEGATIVE.split(",")
)


def _names_a_person(phrase: str) -> bool:
    """Whether a free-text style/effect phrase asks for a person."""
    words = set(str(phrase or "").lower().replace("_", " ").split())
    return bool(words & _PERSON_WORDS)


# ── S4 board ────────────────────────────────────────────────────────────────
async def submit_board(db, comfy, spooler, session: dict[str, Any]) -> dict[str, Any]:
    inputs = _inputs(session)
    workflow = str(inputs.get("board_workflow") or "").strip()
    if not workflow:
        raise MuseError("board_workflow is required")
    if not any((session.get("seed_tags") or {}).values()):
        raise MuseError("compose the tags first")

    count = max(1, int(inputs.get("board_count", 3)))
    session_id = session["session_id"]
    board: dict[str, list[dict[str, Any]]] = {t: [] for t in TRACKS}
    warned_unpatched = False

    for track in TRACKS:
        positive, negative = track_prompt(session, track)
        for index in range(count):
            seed = random.randint(0, (1 << 64) - 1)

            def _attach(tr: str, idx: int):
                async def _inner(sha256: str, meta: dict) -> None:
                    await session_db.attach_board_image(db, session_id, tr, idx, sha256, meta)
                return _inner

            job_id = spooler.submit(
                JobLane.GENERATION,
                f"muse_board:{track}",
                _render_runner(),
                meta={"session_id": session_id, "track": track, "seed_index": index},
                db=db,
                comfy=comfy,
                workflow_name=workflow,
                positive=positive,
                negative=negative,
                width=int(inputs.get("board_width", 512)),
                height=int(inputs.get("board_height", 512)),
                steps=int(inputs.get("board_steps", 16)),
                cfg=float(inputs.get("board_cfg", 3.0)),
                seed=seed,
                subdir=PLAYGROUND_SUBDIR,
                prefix=f"muse_{track}",
                method="muse_board",
                payload_extra={"muse_session_id": session_id, "muse_track": track},
                attach=_attach(track, index),
            )
            board[track].append({
                "seed_index": index, "seed": seed, "job_id": job_id,
                "image_id": "", "pending": True,
            })

    # Tell the user now if this workflow cannot take the board settings, rather
    # than after six full-price renders come back looking nothing like drafts.
    unpatched = _unpatchable(comfy, workflow, inputs)
    if unpatched and not warned_unpatched:
        _warn(session, f"workflow ignores: {', '.join(unpatched)}")

    session["board"] = board
    session["harvest"] = {}
    session["merged"] = {}
    session["status"] = "rendering"
    session_db.log(session, "board", f"{count} per track, {workflow}")
    await session_db.save(db, session)
    return session


def _render_runner():
    from ..jobs.render import run_render
    return run_render


def _unpatchable(comfy, workflow_name: str, inputs: dict[str, Any]) -> list[str]:
    try:
        wf = comfy.load_workflow(workflow_name)
        patchable = comfy.patchable_fields(wf)
    except Exception as exc:
        logger.warning("[muse] could not inspect %s: %s", workflow_name, exc)
        return []
    wanted = {
        "steps": inputs.get("board_steps"),
        "cfg": inputs.get("board_cfg"),
        "width": inputs.get("board_width"),
        "height": inputs.get("board_height"),
    }
    return [k for k, v in wanted.items() if v is not None and not patchable.get(k)]


# ── S5 harvest ──────────────────────────────────────────────────────────────
async def run_harvest(db, session: dict[str, Any], ollama=None) -> dict[str, Any]:
    from pathlib import Path

    inputs = _inputs(session)
    cfg = await get_runtime_config(db)
    threshold = float(inputs.get("harvest_threshold", 0.15))
    model_dir = cfg.get("wd14_model_dir")

    # What the model asked the board to draw. A harvested tag that matches one
    # is the board doing as it was told, which is worth ranking up.
    asked_for = {
        r["tag"].lower()
        for rows in (session.get("slots") or {}).values()
        for r in rows
    }
    frequency = await _vocab_frequency(db) if inputs.get("harvest_rerank") else None

    result: dict[str, list[dict[str, Any]]] = {}
    for track in TRACKS:
        per_image: list[list[dict[str, Any]]] = []
        for slot in (session.get("board") or {}).get(track) or []:
            sha = slot.get("image_id")
            if not sha:
                continue
            doc = await db.get(sha)
            path = Path(str((doc or {}).get("path") or ""))
            if not path.exists():
                logger.warning("[muse] board image missing on disk: %s", sha)
                continue
            per_image.append(await harvest.harvest_image(
                path.read_bytes(),
                threshold=threshold,
                model_dir=model_dir,
                drop_rating_tags=bool(inputs.get("drop_rating_tags", False)),
                drop_character_tags=bool(inputs.get("drop_character_tags", True)),
            ))
        result[track] = harvest.fold_track(
            per_image,
            seed_tags=list(asked_for),
            frequency=frequency,
            rerank=bool(inputs.get("harvest_rerank", False)),
        )

    # Rules got the easy cases. One small model now says which tags belong to
    # the other track, name somebody else's character, or describe the draft's
    # own layout — none of which a frozenset can know.
    dropped: dict[str, list[dict[str, str]]] = {t: [] for t in TRACKS}
    if ollama is not None and inputs.get("llm_cleanup", True):
        identity = list((session.get("character") or {}).get("identity_tags") or [])
        for track in TRACKS:
            result[track], dropped[track] = await cleanup.clean_track(
                result[track], track, ollama,
                theme=str(inputs.get("theme") or ""),
                identity_tags=identity,
                model=str(inputs.get("light_model") or ""),
                num_ctx=cfg.get("ollama_num_ctx"),
            )

    session["harvest"] = result
    session["harvest_dropped"] = dropped
    session["topup"] = []
    session["topup_candidates"] = []
    session["merged"] = {}
    session_db.log(
        session, "harvest",
        " / ".join(f"{t}:{len(result[t])}(-{len(dropped[t])})" for t in TRACKS),
    )
    await session_db.save(db, session)
    return session


async def _vocab_frequency(db) -> dict[str, float]:
    """Danbooru frequency per tag, for the optional re-rank."""
    try:
        from ..db.qdrant_client import WD14_VOCAB_COLLECTION
        out: dict[str, float] = {}
        offset = None
        while True:
            points, offset = await db._qc.scroll(
                collection_name=WD14_VOCAB_COLLECTION,
                limit=1000, offset=offset, with_payload=True, with_vectors=False,
            )
            for p in points:
                payload = p.payload or {}
                name = str(payload.get("name") or "").lower()
                if name:
                    out[name] = float(payload.get("frequency") or 0.0)
            if offset is None:
                break
        return out
    except Exception as exc:
        logger.warning("[muse] vocab frequency scan failed: %s", exc)
        return {}


# ── S4 top-up ───────────────────────────────────────────────────────────────
async def run_topup(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    """Ask retrieval what the theme suggests that the picture does not have."""
    harvested = session.get("harvest") or {}
    if not any(harvested.values()):
        raise MuseError("harvest the board first")

    inputs = _inputs(session)
    cfg = await get_runtime_config(db)
    theme = str(inputs.get("theme") or "").strip()

    # What the picture has is not only what the drafts drew. The composed slots
    # reach the finished prompt too, and a step that cannot see them mistakes
    # them for gaps: `dawn` was in Light, the drafts never rendered an hour, and
    # `night` was duly offered as a way to strengthen the pre-dawn mood.
    present = [r["tag"] for rows in harvested.values() for r in rows]
    for rows in (session.get("slots") or {}).values():
        for row in rows or []:
            tag = str((row or {}).get("tag") or "").strip()
            if tag and tag not in present:
                present.append(tag)
    candidates = await topup.collect_candidates(
        db, ollama,
        theme=theme,
        present=set(present),
        min_score=float(inputs.get("topup_min_score", topup.DEFAULT_MIN_SCORE)),
    )
    picked = await topup.pick_reinforcements(
        candidates, ollama,
        theme=theme,
        present=present,
        model=str(inputs.get("light_model") or ""),
        num_ctx=cfg.get("ollama_num_ctx"),
        picks=int(inputs.get("topup_picks", topup.DEFAULT_PICKS)),
    )
    session["topup_candidates"] = candidates
    session["topup"] = picked
    session["merged"] = {}
    session_db.log(session, "topup", f"{len(picked)}/{len(candidates)}")
    await session_db.save(db, session)
    return session


# ── S6 merge ────────────────────────────────────────────────────────────────
async def run_merge(db, session: dict[str, Any]) -> dict[str, Any]:
    harvested = session.get("harvest") or {}
    if not any(harvested.values()):
        raise MuseError("harvest the board first")

    inputs = _inputs(session)
    cfg = await get_runtime_config(db)
    removal = removal_tag_set(cfg)
    removal |= {
        str(t).strip().lower().replace(" ", "_")
        for t in (session.get("rejected_tags") or []) if str(t).strip()
    }

    character = session.get("character") or {}
    session["merged"] = merge.merge_tracks(
        harvested,
        character_weight=float(inputs.get("character_weight", 0.5)),
        common_ratio=float(inputs.get("merge_common_ratio", 0.5)),
        unique_count=int(inputs.get("merge_unique_count", 30)),
        protected_tags=list(character.get("identity_tags") or []),
        removal=removal,
        reinforcements=[r["tag"] for r in (session.get("topup") or [])],
        must_tags=list(inputs.get("must_tags") or []),
        shot=str(inputs.get("shot") or "auto"),
        angle=str(inputs.get("angle") or "auto"),
        user_slots=user_slots(session),
        composed_slots={
            key: [r["tag"] for r in rows]
            for key, rows in (session.get("slots") or {}).items()
        },
        texts=list(inputs.get("texts") or []),
    )
    session["scene"] = {}
    session_db.log(session, "merge", f"{len(session['merged'].get('tags') or [])} tags")
    await session_db.save(db, session)
    return session


# ── S7 scene ────────────────────────────────────────────────────────────────
async def record_brainstorm(db, session: dict[str, Any], markdown: str) -> dict[str, Any]:
    candidates = scene.parse_brainstorm_sections(markdown)
    session["scene"] = {"candidates": candidates, "markdown": markdown, "chosen": -1, "text": ""}
    session_db.log(session, "brainstorm", scene.summarise_for_log(candidates))
    await session_db.save(db, session)
    return session


async def choose_scene(db, ollama, session: dict[str, Any], index: int) -> dict[str, Any]:
    current = session.get("scene") or {}
    candidates = current.get("candidates") or []
    if not 0 <= index < len(candidates):
        raise MuseError("no such scene idea")

    inputs = _inputs(session)
    cfg = await get_runtime_config(db)
    idea = candidates[index]
    merged = session.get("merged") or {}
    text = await scene.write_prose(
        merged.get("slots") or {},
        ollama,
        model=str(inputs.get("light_model") or ""),
        num_ctx=cfg.get("ollama_num_ctx"),
        idea=f"{idea.get('title', '')}\n{idea.get('body', '')}",
    )
    session["scene"] = {**current, "chosen": index, "text": text}
    # The prompt carries the prose, so it has to be rebuilt once there is some.
    if merged.get("slots"):
        session["merged"] = {
            **merged,
            "positive": slot_defs.render_prompt(
                merged["slots"],
                texts=list(inputs.get("texts") or []),
                prose=text,
            ),
        }
    session_db.log(session, "scene", text)
    await session_db.save(db, session)
    return session


# ── S8 render ───────────────────────────────────────────────────────────────
async def submit_final(db, comfy, spooler, session: dict[str, Any]) -> dict[str, Any]:
    inputs = _inputs(session)
    workflow = str(inputs.get("final_workflow") or "").strip()
    if not workflow:
        raise MuseError("final_workflow is required")
    merged = session.get("merged") or {}
    tags = merged.get("tags") or []
    if not tags:
        raise MuseError("merge the tags first")

    # The slotted prompt already carries its prose and text directives.
    positive = merged.get("positive") or scene.compose_final_prompt(
        tags, (session.get("scene") or {}).get("text", ""),
    )
    negative = str(inputs.get("negative_prompt") or "")
    shot_negative = camera.negative_for(str(inputs.get("shot") or "auto"))
    if shot_negative:
        negative = f"{negative}, {shot_negative}" if negative else shot_negative
    session_id = session["session_id"]

    async def _attach(sha256: str, meta: dict) -> None:
        await session_db.attach_final_image(db, session_id, sha256, meta)

    job_id = spooler.submit(
        JobLane.GENERATION,
        "muse_final",
        _render_runner(),
        meta={"session_id": session_id},
        db=db,
        comfy=comfy,
        workflow_name=workflow,
        positive=positive,
        negative=negative,
        seed=inputs.get("final_seed"),
        subdir="",
        prefix="muse",
        method="muse",
        payload_extra={"muse_session_id": session_id},
        attach=_attach,
    )
    session["final"] = {
        "positive": positive, "negative": negative,
        "job_id": job_id, "image_id": "",
    }
    session["status"] = "rendering"
    session_db.log(session, "render", workflow)
    await session_db.save(db, session)
    return session


async def set_slot(
    db, session: dict[str, Any], slot: str, tags: list[str],
) -> dict[str, Any]:
    """Replace one aspect outright. The user always gets the last word."""
    if slot not in slot_defs.BY_KEY:
        raise MuseError(f"no such slot: {slot}")
    cap = slot_defs.BY_KEY[slot].cap
    cleaned = slot_defs.dedupe_slot(
        [str(t).strip().replace(" ", "_") for t in tags if str(t).strip()], cap,
    )
    session.setdefault("slots", {})[slot] = [
        {"tag": t, "source": "user"} for t in cleaned
    ]
    session["seed_tags"] = _tracks_from_slots(session["slots"])
    session_db.log(session, "slot", f"{slot}={len(cleaned)}")
    await session_db.save(db, session)
    return session


# ── shared ──────────────────────────────────────────────────────────────────
async def reject_tags(
    db, session: dict[str, Any], tags: list[str], *, remove: bool = False,
) -> dict[str, Any]:
    """Edit the exclusion list.

    Rejecting also drops the tag from what is already gathered, so the chip
    disappears immediately. Un-rejecting cannot put it back the same way — the
    tag has to be retrieved again — so the caller re-runs the tags step.
    """
    current = list(session.get("rejected_tags") or [])
    names = [str(t or "").strip() for t in tags if str(t or "").strip()]
    if remove:
        lowered = {n.lower() for n in names}
        current = [t for t in current if t.lower() not in lowered]
    else:
        for name in names:
            if name not in current:
                current.append(name)
    session["rejected_tags"] = current

    blocked = {t.lower() for t in current}
    session["seed_tags"] = {
        track: [r for r in rows if r["tag"].lower() not in blocked]
        for track, rows in (session.get("seed_tags") or {}).items()
    }
    await session_db.save(db, session)
    return session


def _warn(session: dict[str, Any], message: str) -> None:
    warnings = session.setdefault("warnings", [])
    if message not in warnings:
        warnings.append(message)


def view(session: dict[str, Any]) -> dict[str, Any]:
    return public_view(session)
