"""**A window opened from inside Muse comes out above Muse.** (2026-09-13)

Hit by the Showrunner: pressing the lead's name looked as if the roster would not
open. It did open — **behind Muse's panel**. The roster (`CharacterGallery`) is
`--z-panel` (600), Muse's panel is `--z-panel-muse` (640).

The layers are laid out in `frontend/src/style.css`. A layer for windows Muse owns
(`--z-panel-muse-child`, 650) already existed, so the caller passes it in.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "frontend/src/style.css"
PANEL = ROOT / "frontend/src/components/MusePanel.vue"
GALLERY = ROOT / "frontend/src/components/CharacterGallery.vue"


def _layer(name: str) -> int:
    m = re.search(rf"--{re.escape(name)}:\s*(\d+)", CSS.read_text(encoding="utf-8"))
    assert m, f"{name} が style.css に無い"
    return int(m.group(1))


def test_the_muse_owned_layer_is_above_the_muse_shell():
    assert _layer("z-panel-muse-child") > _layer("z-panel-muse") > _layer("z-panel")


@pytest.mark.parametrize("which", ["showPicker", "showPartnerPicker"])
def test_the_roster_opened_from_muse_sits_above_it(which):
    """Both places that open the roster (lead and partner) pass a layer above Muse."""
    src = PANEL.read_text(encoding="utf-8")
    start = src.index(f':show="{which}"')
    block = src[start:start + 400]
    assert 'layer-class="z-[var(--z-panel-muse-child)]"' in block, which


def test_the_roster_takes_the_layer_from_whoever_opens_it():
    """The default stays put (the roster opens from elsewhere too). It goes up only when
    it is passed one."""
    src = GALLERY.read_text(encoding="utf-8")
    assert "layerClass: { type: String, default: 'z-[var(--z-panel)]' }" in src
    assert ':class="layerClass"' in src
    # 根の要素に固定の段を焼き付けていないこと（焼き付けると渡しても効かない）
    root = src[src.index("<template>"):src.index("<template>") + 400]
    assert "z-[var(--z-panel)]" not in root, "根に固定していると上書きできない"
