"""The two caption steps, and the job that walks a bucket through them.

The job runs on the EVALUATION lane: that lane is auto-paused while a render,
a prompt or embeddings are running, so captioning waits for the card by itself
and never lands on top of a render (CLAUDE.md). One job per bucket; pictures
added while it runs are picked up by the same job.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from ..prompt.scene_sheet import _SCRIPT_MODELS
from ..spooler.models import CancelToken, JobCancelled, ProgressReporter
from . import bucket as bk

logger = logging.getLogger(__name__)

#: Hints longer than this are cut; they are recommendations, not the answer.
_HINT_PROMPT_CHARS = 1500
_HINT_TAGS = 40
#: An answer shorter than this did not describe a picture.
_MIN_CAPTION_CHARS = 20


class CaptionError(RuntimeError):
    pass


# ── Prompts ───────────────────────────────────────────────────────────────────

def describe_prompt(*, target_model: str = "krea2", stored_prompt: str = "",
                    tags: list[str] | None = None) -> str:
    spec = _SCRIPT_MODELS.get(target_model, _SCRIPT_MODELS["krea2"])
    lines = [
        f"Write a training caption for this picture. It will be used to train a LoRA "
        f"for {spec['name']}.",
        spec["reads"],
        "- Describe everything you can see: the medium and style first (photograph, "
        "anime illustration, 3D render, ...), then each person or subject — what they "
        "are, what they are doing, pose, hands, face and expression, hair, body, "
        "clothing — then objects, the setting, composition and camera angle, light and colour.",
        "- Write flowing, specific English sentences, the way a good caption reads. "
        "Attach each detail to the thing it belongs to.",
        spec["text"],
        "- Only what is visible. No guesses about names, stories or intentions, no "
        "opinions, no quality words (masterpiece, best quality, beautiful, stunning).",
        "- Do not start with \"The image shows\" or \"This picture\". No headings, no lists, "
        "no markdown. Answer with the caption only.",
    ]
    hints: list[str] = []
    if stored_prompt.strip():
        hints.append("The prompt it was made with (may be wrong or incomplete):\n"
                     + stored_prompt.strip()[:_HINT_PROMPT_CHARS])
    if tags:
        hints.append("Tags a tagger found (noisy; use only what you can confirm):\n"
                     + ", ".join(tags[:_HINT_TAGS]))
    if hints:
        lines.append("")
        lines.append("Recommendations, not instructions — trust the picture over them:")
        lines.extend(hints)
    return "\n".join(lines)


def strip_prompt(full_caption: str, targets: list[str]) -> str:
    listed = "\n".join(f"- {t}" for t in targets)
    return "\n".join([
        "Below is a caption of a picture used to train a LoRA. The LoRA has to learn "
        "the following on its own, through a trigger word:",
        listed,
        "",
        "Rewrite the caption so that it no longer describes any of those things — "
        "not by name and not by their visible features (for a person or character: "
        "their face, eyes, hair, body and other traits that make them who they are; "
        "for a style: the words that describe that style; for an object or outfit: its "
        "parts, colours and materials). Whatever the caption does not describe is what "
        "the trigger word will absorb.",
        "- A style target about colour, shading or lines (\"flat colors\", \"cel shading\", "
        "\"solid colors\") is about how the picture is rendered, not the colours of things in "
        "it. Keep \"dark brown hair\", \"a blue sweater\", \"a white leotard\" as they are; "
        "remove only the words that describe the rendering.",
        "- Keep everything else exactly as written: pose, action, the other people and "
        "things, colours of hair, eyes, clothes and objects, setting, composition, light. "
        "Do not add anything.",
        "- Keep the sentences grammatical; replace a removed subject with a neutral word "
        "(\"a woman\", \"the character\", \"the outfit\") rather than leaving a hole.",
        "- Do not mention that anything was removed. Answer with the caption only.",
        "",
        "Caption:",
        full_caption.strip(),
    ])


_LEAD_RE = re.compile(r"^\s*(?:\*\*)?(?:training )?caption(?:\*\*)?\s*:\s*(?:\*\*)?\s*", re.I)
_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def clean_answer(text: str) -> str:
    """One line of caption text out of whatever the model wrapped it in."""
    text = _THINK_RE.sub("", text or "")
    text = text.strip().strip("`").strip()
    text = _LEAD_RE.sub("", text)
    # Bullet or heading lines are not caption prose; drop their markers.
    lines = [re.sub(r"^\s*(?:[-*#]+\s*)", "", ln).strip() for ln in text.splitlines()]
    text = re.sub(r"\s+", " ", " ".join(ln for ln in lines if ln)).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


# ── Calls ─────────────────────────────────────────────────────────────────────

async def _collect(stream, cancel: CancelToken | None) -> str:
    """Only the answer tokens. Thinking is streamed and discarded (CLAUDE.md:
    non-streaming lets thinking eat the output budget)."""
    out: list[str] = []
    async for event in stream:
        if cancel is not None:
            cancel.raise_if_set()
        if event.get("type") == "token":
            out.append(event["text"])
    return "".join(out)


async def describe(ollama, image: bytes, *, model: str, target_model: str = "krea2",
                   stored_prompt: str = "", tags: list[str] | None = None,
                   cancel: CancelToken | None = None) -> str:
    prompt = describe_prompt(target_model=target_model, stored_prompt=stored_prompt, tags=tags)
    raw = await _collect(
        ollama.generate_vlm_stream(prompt, [image], model=model, think=False), cancel,
    )
    text = clean_answer(raw)
    if len(text) < _MIN_CAPTION_CHARS:
        # A model that cannot read pictures answers with nothing (CLAUDE.md).
        raise CaptionError(
            f"empty answer from {model} — it may not be able to read images"
            if not text else f"answer too short: {text!r}"
        )
    return text


async def strip(ollama, full_caption: str, targets: list[str], *, model: str,
                cancel: CancelToken | None = None) -> str:
    if not targets:
        return full_caption
    raw = await _collect(
        ollama.generate_text_stream(strip_prompt(full_caption, targets), model=model, think=False),
        cancel,
    )
    text = clean_answer(raw)
    if len(text) < _MIN_CAPTION_CHARS:
        raise CaptionError(f"empty answer from {model} while removing the targets")
    return text


# ── Job ───────────────────────────────────────────────────────────────────────

#: Buckets that have a caption job queued or running.
_active: set[str] = set()


def is_captioning(bucket_id: str) -> bool:
    return bucket_id in _active


def ensure_job(spooler, *, db, ollama, bucket: dict) -> str | None:
    """Queue a caption job for the bucket unless one is already on it."""
    from ..spooler.models import JobLane

    bucket_id = bucket["id"]
    if bucket_id in _active or not bk.pending_count(bucket):
        return None
    _active.add(bucket_id)
    try:
        return spooler.submit(
            JobLane.EVALUATION, "lora_caption", run_lora_caption,
            meta={"bucket_id": bucket_id, "bucket_name": bucket["name"]},
            db=db, ollama=ollama, bucket_id=bucket_id,
        )
    except Exception:
        _active.discard(bucket_id)
        raise


async def _caption_model(db) -> str:
    from ..runtime_config import get_runtime_config

    cfg = await get_runtime_config(db)
    return str(cfg.get("lora_caption_model") or "").strip() or cfg["vlm_model"]


async def _read_image(doc: dict) -> bytes:
    """The picture's bytes, from its path or any duplicate of it."""
    candidates = [doc.get("path") or ""] + [d.get("path") or "" for d in doc.get("dup_paths") or []]
    for p in candidates:
        path = Path(p)
        if p and path.is_file():
            return await asyncio.get_running_loop().run_in_executor(None, path.read_bytes)
    raise CaptionError("the picture file is missing")


async def run_lora_caption(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama,
    bucket_id: str,
) -> dict:
    model = await _caption_model(db)
    worked = 0
    errors = 0
    try:
        while True:
            cancel.raise_if_set()
            await cancel.pause_checkpoint()

            async with bk.lock(bucket_id):
                bucket = await bk.load(db, bucket_id)
                item = bk.next_pending(bucket) if bucket else None
                if item is None:
                    # Decided under the lock, so a picture added right now
                    # either is seen here or finds no job and starts one.
                    _active.discard(bucket_id)
                    break
                remaining = bk.pending_count(bucket)
                sha, status = item["sha256"], item["status"]
                full, targets, trigger = item["full_caption"], list(bucket["targets"]), bucket["trigger"]
                target_model = bucket.get("target_model") or "krea2"

            reporter.update(
                worked / (worked + remaining) if worked + remaining else 0.0,
                f"{worked}/{worked + remaining}", key="images",
                done=worked, total=worked + remaining,
            )

            try:
                if status == bk.PENDING_DESCRIBE:
                    doc = await db.get(sha)
                    if not doc:
                        raise CaptionError("the picture is no longer in the gallery")
                    image = await _read_image(doc)
                    text = await describe(
                        ollama, image, model=model, target_model=target_model,
                        stored_prompt=str(doc.get("positive_prompt") or ""),
                        tags=list(doc.get("wd14_tags") or []), cancel=cancel,
                    )
                    async with bk.lock(bucket_id):
                        bucket = await bk.load(db, bucket_id)
                        if bucket is None:
                            break
                        bk.apply_described(bucket, sha, text)
                        await bk.save(db, bucket)
                else:
                    text = bk.with_trigger(
                        trigger, await strip(ollama, full, targets, model=model, cancel=cancel),
                    )
                    async with bk.lock(bucket_id):
                        bucket = await bk.load(db, bucket_id)
                        if bucket is None:
                            break
                        bk.apply_stripped(bucket, sha, text, expected_full=full)
                        await bk.save(db, bucket)
                    worked += 1
            except JobCancelled:
                raise
            except Exception as exc:
                errors += 1
                logger.warning("[lora_caption] %s %s: %s", bucket_id[:8], sha[:12], exc)
                async with bk.lock(bucket_id):
                    bucket = await bk.load(db, bucket_id)
                    if bucket is None:
                        break
                    bk.apply_error(bucket, sha, str(exc) or exc.__class__.__name__)
                    await bk.save(db, bucket)
    finally:
        _active.discard(bucket_id)

    reporter.update(1.0, f"{worked} captions", key="images", done=worked, total=worked)
    return {"bucket_id": bucket_id, "captioned": worked, "errors": errors}
