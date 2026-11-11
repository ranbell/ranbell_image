"""Target models a LoRA bucket can be captioned and exported for.

**A new target is one entry in TARGETS.** What differs between image models is
how their training captions should read (Krea 2 learned from long natural
English; a tag-trained model wants booru tags), so a target owns its describe
prompt. The ids match ``muse.family`` (``krea2``, ``anima``), and the export's
``meta.json`` carries the id so the trainer outside ranbell picks its recipe.

Only targets whose trainer side exists are listed as available; one that is
planned can sit here with ``available=False`` and is refused for new buckets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..prompt.scene_sheet import _SCRIPT_MODELS

_HINT_PROMPT_CHARS = 1500
_HINT_TAGS = 40


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    #: "prose" (natural sentences) or "tags" — how its captions read.
    caption_style: str
    #: Whether a trainer recipe exists for it (external/musubi).
    available: bool
    #: Builds the describe prompt: (stored_prompt, tags) -> prompt.
    describe: Callable[[str, list[str]], str]


def _hints(stored_prompt: str, tags: list[str]) -> list[str]:
    hints: list[str] = []
    if stored_prompt.strip():
        hints.append("The prompt it was made with (may be wrong or incomplete):\n"
                     + stored_prompt.strip()[:_HINT_PROMPT_CHARS])
    if tags:
        hints.append("Tags a tagger found (noisy; use only what you can confirm):\n"
                     + ", ".join(tags[:_HINT_TAGS]))
    if not hints:
        return []
    return ["", "Recommendations, not instructions — trust the picture over them:", *hints]


def _prose_describe(spec_key: str) -> Callable[[str, list[str]], str]:
    def build(stored_prompt: str, tags: list[str]) -> str:
        spec = _SCRIPT_MODELS[spec_key]
        return "\n".join([
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
            *_hints(stored_prompt, tags),
        ])
    return build


TARGETS: dict[str, Target] = {
    "krea2": Target(
        id="krea2", label="Krea 2", caption_style="prose", available=True,
        describe=_prose_describe("krea2"),
    ),
}

DEFAULT_TARGET = "krea2"


def get(target_id: str | None) -> Target:
    """The target, or the default one for an unknown/empty id (old buckets)."""
    return TARGETS.get(str(target_id or ""), TARGETS[DEFAULT_TARGET])


def available() -> list[Target]:
    return [t for t in TARGETS.values() if t.available]
