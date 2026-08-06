"""Measure the probe, then let the model explain it — never the other way round.

This module exists because of one measurement. Handed a board that was 66% pure
black at mean luminance 14 of 255, the VLM described it accurately and then
concluded: *"the exposure is deliberately low-key; in an artistic context it is
correct."* It was not lying, and it was not badly prompted — a model asked to
judge its own output will find a reading in which the output is fine. That is
the whole finding behind "LLMs cannot self-correct without external grounding".

So the split is: numbers decide pass or fail, the model only explains why and
prescribes the fix. The checker is never asked whether the frame is too dark.
It is told that it is, and asked which words did it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import Iterable

logger = logging.getLogger(__name__)

# Below this a pixel carries no readable information; above it, none either.
_BLACK = 16
_WHITE = 240


@dataclass(frozen=True)
class Limits:
    """What a usable frame looks like, in numbers.

    Calibrated against real boards from one session rather than guessed. The
    three that failed measured dead area 86%, 86% and 72%; the two everybody was
    happy with measured 26% and 23%. Plain black fraction does *not* separate
    them — a moody cafe shot that reads perfectly well is 47% below the black
    point, because dark hair and dark clothing are content. What distinguishes a
    broken frame is area that is dark *and* empty.
    """
    luma_min: float = 40.0
    luma_max: float = 195.0
    dead_max: float = 0.40
    white_max: float = 0.15
    ledger_min: float = 0.6


@dataclass(frozen=True)
class Reading:
    mean_luma: float
    black_frac: float
    white_frac: float
    saturation: float
    dead_frac: float
    seen: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    ledger_hit: float = 1.0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_note(self) -> str:
        """The block the checker seat is handed. Facts, framed as facts."""
        lines = [
            "MEASURED FROM THE RENDER (these are facts, not opinions):",
            f"- mean brightness: {self.mean_luma:.0f} of 255",
            f"- dead area (dark and empty): {self.dead_frac * 100:.0f}% of the frame",
            f"- pure black pixels: {self.black_frac * 100:.0f}%",
            f"- blown white: {self.white_frac * 100:.0f}%",
            f"- colour: {self.saturation:.0f} of 255",
        ]
        if self.missing:
            lines.append(
                f"- ledger items NOT visible in the render: {', '.join(self.missing)}"
            )
        if self.seen:
            lines.append(f"- actually visible: {', '.join(self.seen[:20])}")
        lines.append(
            "VERDICT: " + ("PASS" if self.ok else "FAIL — " + "; ".join(self.failures))
        )
        return "\n".join(lines)


# A block is "dead" when it is this dark and this flat: no content, not even a
# dark shape. Grid is coarse on purpose — one dark eye should not read as a void.
_GRID = 16
_DEAD_LUMA = 24.0
_DEAD_STD = 6.0


def _dead_area(grey) -> float:
    """Fraction of the frame that is both near-black and featureless.

    Computed on a coarse grid: block mean and block standard deviation, the
    latter from E[x²] − E[x]². This is the measurement that actually separates
    a broken frame from a dark one — see Limits.
    """
    import numpy as np

    a = np.asarray(grey, dtype=np.float32)
    h, w = a.shape
    bh, bw = h // _GRID, w // _GRID
    if bh < 1 or bw < 1:
        return 0.0
    a = a[: bh * _GRID, : bw * _GRID].reshape(_GRID, bh, _GRID, bw)
    mean = a.mean(axis=(1, 3))
    std = np.sqrt(np.clip((a ** 2).mean(axis=(1, 3)) - mean ** 2, 0, None))
    return float(((mean <= _DEAD_LUMA) & (std <= _DEAD_STD)).mean())


def measure(
    data: bytes,
    *,
    must_appear: Iterable[str] | None = None,
    seen_tags: Iterable[str] | None = None,
    limits: Limits | None = None,
    check_exposure: bool = True,
) -> Reading:
    """Numbers first, verdict second.

    ``check_exposure`` is off for the pose probe: that one is rendered on a
    white background on purpose, so its brightness says nothing about the
    picture the crew is building.
    """
    from PIL import Image, ImageStat

    lim = limits or Limits()
    with Image.open(BytesIO(data)) as img:
        rgb = img.convert("RGB")
        grey = rgb.convert("L")
        hist = grey.histogram()
        total = max(sum(hist), 1)
        black_frac = sum(hist[:_BLACK]) / total
        white_frac = sum(hist[_WHITE:]) / total
        mean_luma = ImageStat.Stat(grey).mean[0]
        saturation = ImageStat.Stat(rgb.convert("HSV")).mean[1]
        dead_frac = _dead_area(grey)

    wanted = [str(m).strip() for m in (must_appear or []) if str(m).strip()]
    visible = [str(t).strip() for t in (seen_tags or []) if str(t).strip()]
    hay = " ".join(visible).lower().replace("_", " ")
    missing = [w for w in wanted if w.lower().replace("_", " ") not in hay]
    hit = 1.0 if not wanted else (len(wanted) - len(missing)) / len(wanted)

    failures: list[str] = []
    if check_exposure:
        if mean_luma < lim.luma_min:
            failures.append(
                f"too dark (brightness {mean_luma:.0f}, needs ≥{lim.luma_min:.0f})")
        elif mean_luma > lim.luma_max:
            failures.append(
                f"too bright (brightness {mean_luma:.0f}, needs ≤{lim.luma_max:.0f})")
        if dead_frac > lim.dead_max:
            failures.append(
                f"{dead_frac * 100:.0f}% of the frame is empty black "
                f"(max {lim.dead_max * 100:.0f}%)"
            )
        if white_frac > lim.white_max:
            failures.append(f"{white_frac * 100:.0f}% is blown out")
    if wanted and hit < lim.ledger_min:
        failures.append(f"{len(missing)} of {len(wanted)} ledger objects did not render")

    return Reading(
        mean_luma=mean_luma, black_frac=black_frac, white_frac=white_frac,
        saturation=saturation, dead_frac=dead_frac,
        seen=visible, missing=missing, ledger_hit=hit, failures=failures,
    )
