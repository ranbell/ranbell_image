"""**呼んでいる関数が実在するか。**

実際にやった（2026-08-29）: `pick_partner` に `ollama` を通したとき、API 側で
`_ollama(request)` と書いた —— **そんな補助関数は無かった**。相方の登録が
実行時 `NameError` で 500 になったが、**全 1729 件の試験は緑のまま**通った。
どの試験もその経路を踏んでいなかった。

実行時にしか出ない名前の間違いを、読むだけで拾う。関数の中で使っている名前が
モジュールにも組み込みにも無ければ落とす。
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
_MUSE = root_dir / "backend" / "app" / "muse"


def _bound(node: ast.AST) -> set[str]:
    """その関数の中で束縛される名前（引数・代入・for・with・except・内包）。"""
    out: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
            out.add(arg.arg)
        for extra in (a.vararg, a.kwarg):
            if extra:
                out.add(extra.arg)
    for sub in ast.walk(node):
        # **入れ子の関数と lambda の引数も束縛。** ここを落とすと、内側の
        # 引数が「未定義」に見える
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = sub.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
                out.add(arg.arg)
            for extra in (a.vararg, a.kwarg):
                if extra:
                    out.add(extra.arg)
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            out.add(sub.id)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(sub.name)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            out.add(sub.name)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for alias in sub.names:
                out.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(sub, ast.Global) or isinstance(sub, ast.Nonlocal):
            out.update(sub.names)
    return out


def _module_names(tree: ast.Module) -> set[str]:
    out: set[str] = set(dir(builtins))
    out |= _bound(tree)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
    return out


@pytest.mark.parametrize("path", sorted(_MUSE.glob("*.py")), ids=lambda p: p.name)
def test_every_name_a_function_uses_exists(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    known = _module_names(tree)
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local = known | _bound(node)
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
                    and sub.id not in local):
                missing.append(f"{node.name}: {sub.id} (行 {sub.lineno})")
    assert not missing, f"{path.name} に未定義の名前がある: {missing[:8]}"
