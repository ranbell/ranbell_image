"""**条件の中でだけ作った変数を、外で読まない。**（2026-09-18）

総監督から実機のログ:

    File "/app/app/muse/service.py", line 945, in chat
        if floor:
    UnboundLocalError: cannot access local variable 'floor'

`floor` は `if crew_room.has_crew(session):` の中でだけ作られていたので、
**班の居ない回（一人撮り・W撮り）は会話が 500 で落ちていた**。2026-09-14 の
`0019b5b` から四日間、誰も気づかなかった —— Python は実際にその道を通るまで
教えてくれないし、`pyflakes` も `ruff` もこの形は見ない。

だから**読む側で数える**。関数ごとに「必ず束縛される名前」を集め、その外で
読まれている名前を探す:

    必ず束縛      関数の最上位の代入・`with … as`・両側で代入する `if`/`try`
    条件つき束縛   `if` の片側だけ・`for` の中・例外側が抜ける `try` の本体

実際の `floor` を再現できることは `test_a_plain_turn_runs.py` が見ている。
こちらは**同じ形が他に無いこと**を、muse のコード全体で保つための一本。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MUSE = ROOT / "backend/app/muse"


def _surely_bound(body: list[ast.stmt]) -> set[str]:
    """この並びを通れば**必ず**束縛されている名前。"""
    out: set[str] = set()
    for st in body:
        if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = st.targets if isinstance(st, ast.Assign) else [st.target]
            for t in targets:
                out |= {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
        elif isinstance(st, (ast.With, ast.AsyncWith)):
            for item in st.items:
                if item.optional_vars is not None:
                    out |= {n.id for n in ast.walk(item.optional_vars)
                            if isinstance(n, ast.Name)}
            out |= _surely_bound(st.body)
        elif isinstance(st, ast.If) and st.orelse:
            out |= _surely_bound(st.body) & _surely_bound(st.orelse)
        elif isinstance(st, ast.Try):
            out |= _surely_bound(st.finalbody)
            exits = all(
                h.body and isinstance(h.body[-1], (ast.Return, ast.Raise, ast.Continue))
                for h in st.handlers
            )
            if st.handlers and exits:
                out |= _surely_bound(st.body)       # 例外側は必ず抜ける
            elif st.handlers:
                safe = _surely_bound(st.body)
                for h in st.handlers:
                    safe &= _surely_bound(h.body)   # どちらを通っても在る
                out |= safe
    return out


def _comprehension_names(fn: ast.AST) -> set[str]:
    """内包表記の変数はそのカッコの中だけ。外の話ではない。"""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in node.generators:
                out |= {n.id for n in ast.walk(gen.target) if isinstance(n, ast.Name)}
    return out


def _arg_names(fn) -> set[str]:
    a = fn.args
    names = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def unbound_reads(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        sure = _surely_bound(fn.body) | _arg_names(fn)
        stored = {n.id for n in ast.walk(fn)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        maybe = stored - sure - _comprehension_names(fn)
        if not maybe:
            continue
        for st in fn.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            before = _surely_bound([s for s in fn.body if s.lineno < st.lineno])
            for node in ast.walk(st):
                if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
                    continue
                if node.id not in maybe or node.id in before:
                    continue
                # その文が自分で束縛しているなら（ループ変数など）見送る
                if any(isinstance(m, ast.Name) and m.id == node.id
                       and isinstance(m.ctx, ast.Store) for m in ast.walk(st)):
                    continue
                hits.append(f"{path.name}:{node.lineno} {fn.name}() が {node.id!r} を"
                            "条件の外で読んでいる")
    return hits


def test_no_conditional_local_is_read_outside_its_branch():
    found: list[str] = []
    for path in sorted(MUSE.glob("*.py")):
        found += unbound_reads(path)
    assert not found, "条件つきの変数を外で読んでいる:\n" + "\n".join(found)


def test_the_scan_catches_the_shape_it_is_meant_to_catch(tmp_path):
    """**試験そのものが効いていること。** 実機で踏んだ形をそのまま置いて確かめる。"""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "async def chat(session):\n"
        "    if session.get('crew'):\n"
        "        floor = walk()\n"
        "    if floor:\n"
        "        land(floor)\n",
        encoding="utf-8",
    )
    assert any("floor" in h for h in unbound_reads(bad))

    good = tmp_path / "good.py"
    good.write_text(
        "async def chat(session):\n"
        "    floor = []\n"
        "    if session.get('crew'):\n"
        "        floor = walk()\n"
        "    if floor:\n"
        "        land(floor)\n",
        encoding="utf-8",
    )
    assert unbound_reads(good) == []
