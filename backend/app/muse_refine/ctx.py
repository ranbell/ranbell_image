"""文脈長を一箇所で決める。**判定係と揃えるため。**（2026-09-10）

総監督「純粋に推論に時間がかかっていると思う。Muse の会話ターンで 20-30sec
かかるようです」。実体はモデルの読み直しだった。

Ollama は**文脈長が違うと別インスタンスとして読み直す**。実測（26B・実機）:

    同じ長さを続ける    1回目 21.8s（読込 20.4s）→ 2回目 0.2s（読込 0.0s）
    長さを交互に変える   毎回 12.8s（読込 11.3s）

Refine は `num_ctx` を一つも渡しておらず既定値、判定係（`muse.chain._call`）は
`ollama_num_ctx`（16384）を渡す。1ターンの中で

    判定係 16384 → nsfw 16384 → abuse 16384 → writer 既定 → 女優 既定 → verify 既定

と交互になり、**最低2回、11〜24秒の読み込み**が乗っていた。生成そのものは
35〜48tps で正常。

`muse.service._num_ctx` と同じ式を使う —— **同じ数字でなければ意味がない**。
"""
from __future__ import annotations

from typing import Any


def refine_num_ctx(session: dict[str, Any] | None) -> int | None:
    """このセッションで使う文脈長。判定係と同じ値になるように。"""
    inputs = dict((session or {}).get("inputs") or {})
    cfg = dict((session or {}).get("_runtime_cfg") or {})
    return int(inputs.get("num_ctx") or cfg.get("ollama_num_ctx") or 0) or None
