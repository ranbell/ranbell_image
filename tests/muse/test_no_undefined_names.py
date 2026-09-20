"""**Does the function being called actually exist?**

Really done (2026-08-29): while threading `ollama` into `pick_partner`, the API side
was written as `_ollama(request)` — **there was no such helper**. Registering a
partner became a runtime `NameError` and a 500, and **all 1,729 tests stayed green**.
Not one of them walked that path.

A name mistake that only shows at runtime is caught by reading. If a name used
inside a function is neither in the module nor a builtin, this fails.
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
    """The names bound inside that function (arguments, assignment, `for`, `with`,
    `except`, comprehensions)."""
    out: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
            out.add(arg.arg)
        for extra in (a.vararg, a.kwarg):
            if extra:
                out.add(extra.arg)
    for sub in ast.walk(node):
        # **A nested function's and a lambda's arguments bind too.** Miss this and
        # the inner arguments look "undefined"
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
