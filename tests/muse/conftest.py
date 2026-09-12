"""`backend` を import path に入れる。

このスイートのテストは `from app.muse import …` と直に書いてある。
他のスイートは各ファイルの頭で `sys.path.insert` しているが、ここは無いので
そのままでは 11 本すべてが収集時に落ちる（`ModuleNotFoundError: app`）。

**作者のファイルには触らず、入口を一つ置く。**
"""
import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
