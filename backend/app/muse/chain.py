"""One LLM turn per seat: SAY plus labelled lines.

Every seat answers in the same shape, so there is one parser and one runner
rather than a function per role. That is not tidiness for its own sake — the
previous design had each seat re-emit the entire prompt, and a model retyping
fifty tags on its seventeenth pass turned `tatami_mat` into `tat_mat` with
nobody downstream left to catch it. Labelled lines mean a seat only ever types
the part it owns.

The parser is deliberately forgiving about label spelling (`**PLACE** :`, full-
width colons, leading bullets). A run that loses its place because a model wrote
`MUST APPEAR :` is a run wasted.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import crew, identity

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).parent / "prompts"

TokenCallback = Callable[[str], None]


@dataclass(frozen=True)
class SeatTurn:
    seat: str
    say: str
    fields: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    # True when the seat was offered a render and could not read it. A model
    # without vision does not error — it returns nothing — so the retry is
    # silent unless somebody surfaces this.
    blind: bool = False


class ChainError(Exception):
    """A turn produced nothing usable."""


def system_prompt(filename: str) -> str:
    return (PROMPTS / filename).read_text(encoding="utf-8").rstrip("\n")


def _label_pattern(labels: tuple[str, ...]) -> re.Pattern[str]:
    alts = "|".join(sorted((lb.replace(" ", r"\s*") for lb in labels), key=len, reverse=True))
    return re.compile(
        r"(?im)^[\s>*_#-]*(" + alts + r")[\s*_]*[:：]\s*(.*?)\s*$"
    )


def parse_seat(raw: str, labels: tuple[str, ...], list_labels: frozenset[str]) -> tuple[str, dict]:
    """Return (say, fields). Missing or blank labels are simply absent.

    A seat that has nothing to add is expected to leave its lines blank — the
    reduce seat cutting nothing is a real answer, not a failed turn — so blanks
    are dropped rather than treated as an error.
    """
    text = (raw or "").strip()
    if not text:
        return "", {}
    matches = list(_label_pattern(labels).finditer(text))

    say = text[: matches[0].start()] if matches else text
    say = re.sub(r"(?is)^\s*SAY\s*[:：]\s*", "", say.strip()).strip()

    out: dict[str, Any] = {}
    for m in matches:
        key = re.sub(r"\s+", " ", m.group(1)).upper()
        value = m.group(2).strip().strip("*_").strip()
        if not value or key in out or value.lower() in ("(none)", "none", "-", "n/a"):
            continue
        if key in list_labels:
            items = [v.strip().strip("*_") for v in value.split(",")]
            items = [v for v in items if v]
            if items:
                out[key] = items
        else:
            out[key] = value
    return say, out


async def _call(
    ollama, *, system: str, prompt: str, model: str,
    images: list[bytes] | None, num_ctx: int | None,
    think: bool, on_token: TokenCallback | None = None,
) -> str:
    options: dict[str, Any] = {"num_predict": -1}
    if num_ctx:
        options["num_ctx"] = int(num_ctx)
    kwargs = dict(model=model, options=options, system=system, think=think)

    stream = (ollama.generate_vlm_stream(prompt, images, **kwargs) if images
              else ollama.generate_text_stream(prompt, **kwargs))
    parts: list[str] = []
    async for event in stream:
        if event.get("type") == "token" and event.get("text"):
            parts.append(event["text"])
            if on_token is not None:
                try:
                    on_token(event["text"])
                except Exception:
                    logger.debug("[muse.chain] on_token failed", exc_info=True)

    text = "".join(parts).strip()
    if not text:
        raise ChainError("the model returned an empty answer")
    return text


async def _call_seeing(
    ollama, *, system: str, prompt: str, model: str,
    images: list[bytes] | None, num_ctx: int | None, think: bool,
    on_token: TokenCallback | None = None,
) -> tuple[str, bool]:
    """Call with the render attached, falling back to text when it cannot read it.

    A model without vision does not refuse the image — it returns an empty
    response, which reads exactly like a bad turn. One retry without the picture
    keeps the crew moving; the flag lets the caller say so out loud rather than
    quietly degrading for the rest of the session.
    """
    if not images:
        return await _call(
            ollama, system=system, prompt=prompt, model=model, images=None,
            num_ctx=num_ctx, think=think, on_token=on_token,
        ), False
    try:
        return await _call(
            ollama, system=system, prompt=prompt, model=model, images=images,
            num_ctx=num_ctx, think=think, on_token=on_token,
        ), False
    except ChainError:
        logger.warning(
            "[muse.chain] %s returned nothing for an image turn — retrying blind",
            model,
        )
    return await _call(
        ollama, system=system, prompt=prompt, model=model, images=None,
        num_ctx=num_ctx, think=think, on_token=on_token,
    ), True


async def run_seat(
    ollama, *, seat: str, user_prompt: str, model: str,
    num_ctx: int | None = None,
    images: list[bytes] | None = None,
    character: dict[str, Any] | None = None,
    style: str = "", think: bool = False,
    on_token: TokenCallback | None = None,
) -> SeatTurn:
    """One seat speaks and writes its own labelled lines."""
    rid = crew.role_of(seat)
    if not rid:
        raise ChainError(f"unknown seat: {seat}")
    raw, blind = await _call_seeing(
        ollama,
        system=crew.system_prompt_for(rid, character=character, style=style),
        prompt=user_prompt, model=model, images=images,
        num_ctx=num_ctx, think=think, on_token=on_token,
    )
    say, fields = parse_seat(raw, crew.FIELDS[rid], crew.LIST_FIELDS)
    return SeatTurn(seat=rid, say=say, fields=fields, raw=raw, blind=blind)


def slot_delta(turn: SeatTurn) -> tuple[dict[str, Any], list[str]]:
    """Split one seat's answer into (writes, deletions) for the shot sheet."""
    add: dict[str, Any] = {}
    remove: list[str] = []
    for label, value in turn.fields.items():
        if label == "REMOVE":
            remove.extend(value if isinstance(value, list) else [value])
            continue
        slot = crew.FIELD_SLOT.get(label)
        if slot:
            add[slot] = value
    return add, remove


def apply_turn(shot: dict[str, Any], turn: SeatTurn) -> dict[str, Any]:
    """Fold a seat's answer into the sheet, honouring which slots it owns."""
    add, remove = slot_delta(turn)
    owns = crew.ROLES[turn.seat]["owns"]
    return identity.apply_delta(
        shot,
        add=add,
        remove=remove,
        # The planner and the lead settle their slots outright; enrich only ever
        # appends. Reduce and check own nothing and may only delete.
        allowed=owns,
        list_slots=crew.LIST_SLOTS,
        overwrite=turn.seat in ("plan", "actress"),
    )


# ── legacy names a few older tests still import ─────────────────────────────
@dataclass(frozen=True)
class StageResult:
    prompt: str
    pose_intent: str = ""


REFINE_STAGES: tuple[tuple[str, str], ...] = (
    ("reinforce", "b_reinforce.md"),
    ("cinematic", "c_cinematic.md"),
    ("angle", "d_angle.md"),
)


def stages_for(count: int) -> tuple[tuple[str, str], ...]:
    return REFINE_STAGES[:max(1, min(int(count), len(REFINE_STAGES)))]
