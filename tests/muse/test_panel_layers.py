"""**Muse の中から開く窓は、Muse より上に出す。**（2026-09-13）

総監督が踏んだ: 主演の名前を押しても名簿が開かないように見えた。実際には開いて
いて、**Muse の画面の裏に出ていた** —— 名簿（`CharacterGallery`）は
`--z-panel`（600）、Muse の画面は `--z-panel-muse`（640）。

段は `frontend/src/style.css` に並べてある。Muse が持つ窓のための段
（`--z-panel-muse-child` 650）が既にあったので、呼ぶ側から渡す。
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
    """名簿を開く二枚（主演・相方）とも、Muse より上の段を渡していること。"""
    src = PANEL.read_text(encoding="utf-8")
    start = src.index(f':show="{which}"')
    block = src[start:start + 400]
    assert 'layer-class="z-[var(--z-panel-muse-child)]"' in block, which


def test_the_roster_takes_the_layer_from_whoever_opens_it():
    """既定は据え置き（名簿は他からも開く）。渡されたときだけ上へ。"""
    src = GALLERY.read_text(encoding="utf-8")
    assert "layerClass: { type: String, default: 'z-[var(--z-panel)]' }" in src
    assert ':class="layerClass"' in src
    # 根の要素に固定の段を焼き付けていないこと（焼き付けると渡しても効かない）
    root = src[src.index("<template>"):src.index("<template>") + 400]
    assert "z-[var(--z-panel)]" not in root, "根に固定していると上書きできない"
