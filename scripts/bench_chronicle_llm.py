#!/usr/bin/env python3
"""Benchmark Chronicle Stage1/Stage2 LLM wall-clock against live Ollama.

Uses the same prompt builders as production (prompt_assets + stage1/stage2).
Does NOT generate images.

Examples:
  PYTHONPATH=backend python scripts/bench_chronicle_llm.py \\
    --url http://192.168.53.10:11434 --model gemma4:e2b

  PYTHONPATH=backend python scripts/bench_chronicle_llm.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

from app.story import prompt_assets  # noqa: E402
from app.story.stage1_storyboard import (  # noqa: E402
    build_stage1_messages,
    build_stage1_user_input,
    parse_stage1_json,
)
from app.story.stage2_enhance import build_stage2_prompt  # noqa: E402


SAMPLE_PROFILE = {
    "hair_color": "brown_hair",
    "hairstyle": "medium_hair",
    "eye_color": "brown_eyes",
    "base_outfit": "school_uniform",
}

SAMPLE_THEME = "放課後の図書室で、雨音を聞きながら課題を進める一日"


def _chars(*parts: str) -> int:
    return sum(len(p or "") for p in parts)


def _approx_tokens(n_chars: int) -> int:
    return max(1, n_chars // 3)


def prompt_size_report(with_fewshot: bool) -> dict[str, Any]:
    system = prompt_assets.stage1_system_prompt()
    few = prompt_assets.stage1_fewshots_block() if with_fewshot else ""
    user_input = build_stage1_user_input(
        theme=SAMPLE_THEME,
        character_profile=SAMPLE_PROFILE,
        include_happening=False,
        author_style="slice_of_life soft focus, gentle pacing",
        custom_tags={},
        avoid_repeats=[],
        style_hint="",
    )
    if with_fewshot:
        messages = build_stage1_messages(user_input)
    else:
        payload = json.dumps(user_input, ensure_ascii=False, indent=2)
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "# RUNTIME INPUT (JSON)\n"
                    f"{payload}\n\n"
                    "Follow the SYSTEM rules. Output one JSON object only.\n"
                ),
            },
        ]
    user = messages[1]["content"]
    stage2_sample = build_stage2_prompt(
        panel={
            "camera": "medium_shot",
            "character_state_diff": "slightly damp sleeves",
            "act": "studying",
            "narrative_ja": "雨音の中、ノートにペンを走らせる。",
            "narrative_en": "She writes in her notebook under rain sounds.",
            "character_focus": "hands and notebook",
            "gesture": "holding_pen",
            "time_marker": "late afternoon",
            "visible_elements": ["notebook", "window", "rain"],
            "danbooru_tags": ["sitting", "indoors", "window"],
        },
        panel_key="panel_1",
        consistency_tags=["brown_hair", "medium_hair", "brown_eyes", "school_uniform"],
        custom_tags=[],
        shared_tags=["indoors"],
        title="Rain Study",
        core_conflict="finish homework before closing",
    )
    return {
        "stage1_system_chars": len(system),
        "stage1_fewshot_chars": len(few),
        "stage1_user_chars": len(user),
        "stage1_total_chars": _chars(system, user),
        "stage1_approx_tokens": _approx_tokens(_chars(system, user)),
        "stage2_prompt_chars": len(stage2_sample),
        "stage2_approx_tokens": _approx_tokens(len(stage2_sample)),
        "with_fewshot": with_fewshot,
    }


def _meta_from_chat_response(data: dict) -> dict[str, Any]:
    keys = (
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
    )
    out = {k: data.get(k) for k in keys if k in data}
    if out.get("total_duration"):
        out["total_duration_sec"] = round(out["total_duration"] / 1e9, 3)
    if out.get("eval_duration") and out.get("eval_count"):
        ns = out["eval_duration"]
        if ns:
            out["tokens_per_sec"] = round(out["eval_count"] / (ns / 1e9), 2)
    return out


async def chat_timed(
    client: httpx.AsyncClient,
    *,
    url: str,
    model: str,
    messages: list[dict[str, str]],
    options: dict[str, Any],
    think: bool,
    store_text: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": {"num_predict": -1, **options},
    }
    t0 = time.perf_counter()
    r = await client.post(f"{url.rstrip('/')}/api/chat", json=payload)
    wall = time.perf_counter() - t0
    if r.is_error:
        return {
            "ok": False,
            "wall_sec": round(wall, 3),
            "error": f"{r.status_code}: {r.text[:400]}",
        }
    data = r.json()
    msg = data.get("message") or {}
    text = str(msg.get("content") or "").strip()
    if not text:
        thinking = str(msg.get("thinking") or data.get("thinking") or "").strip()
        if thinking and "{" in thinking:
            text = thinking
    out: dict[str, Any] = {
        "ok": True,
        "wall_sec": round(wall, 3),
        "out_chars": len(text),
        "parsed_ok": parse_stage1_json(text) is not None,
        "ollama": _meta_from_chat_response(data),
        "preview": text[:200].replace("\n", " "),
    }
    if store_text:
        out["text"] = text
    return out


def stage1_messages(*, with_fewshot: bool) -> list[dict[str, str]]:
    user_input = build_stage1_user_input(
        theme=SAMPLE_THEME,
        character_profile=SAMPLE_PROFILE,
        include_happening=False,
        author_style="slice_of_life soft focus, gentle pacing",
        custom_tags={},
        avoid_repeats=[],
        style_hint="",
    )
    if with_fewshot:
        return build_stage1_messages(user_input)
    system = prompt_assets.stage1_system_prompt()
    payload = json.dumps(user_input, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "# RUNTIME INPUT (JSON)\n"
                f"{payload}\n\n"
                "Follow the SYSTEM rules. Output one JSON object only.\n"
            ),
        },
    ]


def stage2_messages_from_stage1(stage1: dict[str, Any] | None) -> list[dict[str, str]]:
    if stage1 and isinstance(stage1.get("panels"), list) and stage1["panels"]:
        panel = stage1["panels"][0]
        consistency = list(stage1.get("consistency_tags") or [])
        shared = list(stage1.get("shared_tags") or [])
        title = str(stage1.get("title") or "")
        conflict = str(stage1.get("core_conflict") or "")
    else:
        panel = {
            "camera": "medium_shot",
            "character_state_diff": "slightly damp sleeves",
            "act": "studying",
            "narrative_ja": "雨音の中、ノートにペンを走らせる。",
            "narrative_en": "She writes in her notebook under rain sounds.",
            "character_focus": "hands and notebook",
            "gesture": "holding_pen",
            "time_marker": "late afternoon",
            "visible_elements": ["notebook", "window", "rain"],
            "danbooru_tags": ["sitting", "indoors", "window"],
        }
        consistency = ["brown_hair", "medium_hair", "brown_eyes", "school_uniform"]
        shared = ["indoors"]
        title = "Rain Study"
        conflict = "finish homework before closing"
    prompt = build_stage2_prompt(
        panel=panel if isinstance(panel, dict) else {},
        panel_key="panel_1",
        consistency_tags=consistency,
        custom_tags=[],
        shared_tags=shared,
        title=title,
        core_conflict=conflict,
    )
    return [{"role": "user", "content": prompt}]


async def run_case(
    client: httpx.AsyncClient,
    *,
    name: str,
    url: str,
    model: str,
    messages: list[dict[str, str]],
    options: dict[str, Any],
    think: bool,
) -> dict[str, Any]:
    print(f"  [{name}] ...", flush=True)
    run = await chat_timed(
        client,
        url=url,
        model=model,
        messages=messages,
        options=options,
        think=think,
        store_text=True,
    )
    print(
        f"    wall={run.get('wall_sec')}s ok={run.get('ok')} "
        f"parsed={run.get('parsed_ok')} "
        f"prompt_eval={((run.get('ollama') or {}).get('prompt_eval_count'))} "
        f"eval={((run.get('ollama') or {}).get('eval_count'))}",
        flush=True,
    )
    # Drop full text from JSON report (keep preview only)
    text = run.pop("text", "")
    return {
        "case": name,
        "in_chars": _chars(*(m.get("content") or "" for m in messages)),
        "wall_sec_mean": run.get("wall_sec"),
        "wall_sec_sum": run.get("wall_sec"),
        "runs": [run],
        "_text": text,
    }


async def run_stage1_x3(
    client: httpx.AsyncClient,
    *,
    url: str,
    model: str,
    options: dict[str, Any],
    think: bool,
    with_fewshot: bool,
) -> dict[str, Any]:
    print("  [stage1_x3] 3 sequential candidates ...", flush=True)
    parts: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for i, cid in enumerate(("A", "B", "C")):
        opts = dict(options)
        opts["temperature"] = min(1.2, float(opts.get("temperature") or 0.7) + 0.08 * i)
        messages = stage1_messages(with_fewshot=with_fewshot)
        print(f"    candidate {cid} ...", flush=True)
        one = await chat_timed(
            client,
            url=url,
            model=model,
            messages=messages,
            options=opts,
            think=think,
        )
        one["candidate_id"] = cid
        parts.append(one)
        print(f"      wall={one.get('wall_sec')}s ok={one.get('ok')}", flush=True)
    wall = time.perf_counter() - t0
    ok_walls = [p["wall_sec"] for p in parts if p.get("ok")]
    return {
        "case": "stage1_x3",
        "with_fewshot": with_fewshot,
        "in_chars": _chars(*(m.get("content") or "" for m in stage1_messages(with_fewshot=with_fewshot))),
        "wall_sec_sum": round(wall, 3),
        "wall_sec_mean": round(sum(ok_walls) / len(ok_walls), 3) if ok_walls else None,
        "wall_sec_parts_sum": round(sum(ok_walls), 3) if ok_walls else None,
        "runs": parts,
    }


async def run_stage2_x3(
    client: httpx.AsyncClient,
    *,
    url: str,
    model: str,
    options: dict[str, Any],
    think: bool,
    stage1: dict[str, Any] | None,
) -> dict[str, Any]:
    if stage1 and isinstance(stage1.get("panels"), list) and len(stage1["panels"]) >= 3:
        panels = stage1["panels"]
        consistency = list(stage1.get("consistency_tags") or [])
        shared = list(stage1.get("shared_tags") or [])
        title = str(stage1.get("title") or "")
        conflict = str(stage1.get("core_conflict") or "")
    else:
        panels = [None, None, None]
        consistency = ["brown_hair", "medium_hair", "brown_eyes", "school_uniform"]
        shared = ["indoors"]
        title = "Rain Study"
        conflict = "finish homework"

    def _msgs(i: int) -> list[dict[str, str]]:
        panel = panels[i] if i < len(panels) and isinstance(panels[i], dict) else {
            "camera": ("long_shot", "medium_shot", "close_up")[i],
            "narrative_ja": f"パネル{i + 1}",
            "danbooru_tags": ["sitting"],
        }
        prompt = build_stage2_prompt(
            panel=panel,
            panel_key=f"panel_{i + 1}",
            consistency_tags=consistency,
            custom_tags=[],
            shared_tags=shared,
            title=title,
            core_conflict=conflict,
        )
        return [{"role": "user", "content": prompt}]

    print("  [stage2_x3_parallel] ...", flush=True)
    t0 = time.perf_counter()
    parallel = await asyncio.gather(*[
        chat_timed(
            client,
            url=url,
            model=model,
            messages=_msgs(i),
            options=options,
            think=think,
        )
        for i in range(3)
    ])
    parallel_wall = time.perf_counter() - t0
    print(f"    parallel wall={parallel_wall:.3f}s", flush=True)

    print("  [stage2_x3_sequential] ...", flush=True)
    seq: list[dict[str, Any]] = []
    t1 = time.perf_counter()
    for i in range(3):
        one = await chat_timed(
            client,
            url=url,
            model=model,
            messages=_msgs(i),
            options=options,
            think=think,
        )
        seq.append(one)
        print(f"    panel_{i + 1} wall={one.get('wall_sec')}s", flush=True)
    seq_wall = time.perf_counter() - t1

    return {
        "case": "stage2_x3",
        "in_chars": _chars(*(_msgs(0)[0]["content"],)),
        "parallel_wall_sec": round(parallel_wall, 3),
        "sequential_wall_sec": round(seq_wall, 3),
        "wall_sec_sum": round(parallel_wall, 3),
        "parallel_runs": parallel,
        "sequential_runs": seq,
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    print("\n=== SUMMARY ===")
    print(f"{'case':<28} {'wall_s':>8} {'in_chars':>10} {'notes'}")
    print("-" * 72)
    for r in rows:
        case = r.get("case") or "?"
        wall = r.get("wall_sec_mean") or r.get("wall_sec_sum") or r.get("parallel_wall_sec")
        inch = r.get("in_chars") or ""
        notes = ""
        if "with_fewshot" in r:
            notes = f"fewshot={r['with_fewshot']}"
        if case == "stage2_x3":
            notes = f"par={r.get('parallel_wall_sec')} seq={r.get('sequential_wall_sec')}"
        print(f"{case:<28} {wall if wall is not None else '-':>8} {inch!s:>10} {notes}")


async def main_async(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "url": args.url,
        "model": args.model,
        "think": args.think,
        "num_ctx": args.num_ctx,
        "temperature": args.temperature,
        "cases": {},
        "prompt_sizes": {
            "with_fewshot": prompt_size_report(True),
            "without_fewshot": prompt_size_report(False),
        },
    }
    summary_rows: list[dict[str, Any]] = []

    print("Prompt sizes:")
    for label, ps in report["prompt_sizes"].items():
        print(
            f"  {label}: stage1_total={ps['stage1_total_chars']} chars "
            f"(~{ps['stage1_approx_tokens']} tok), "
            f"fewshot={ps['stage1_fewshot_chars']}, "
            f"stage2={ps['stage2_prompt_chars']}"
        )

    if args.dry_run:
        out = Path(args.out)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {out} (dry-run)")
        return 0

    options = {
        "temperature": args.temperature,
        "num_ctx": args.num_ctx,
    }
    want = set(args.cases) if "all" not in args.cases else {
        "stage1_once",
        "stage1_once_no_fewshot",
        "stage1_x3",
        "stage2_once",
        "stage2_x3",
    }

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        try:
            ver = await client.get(f"{args.url.rstrip('/')}/api/version")
            ver.raise_for_status()
            print(f"Ollama OK: {ver.json()}")
        except Exception as e:
            print(f"ERROR: cannot reach Ollama at {args.url}: {e}", file=sys.stderr)
            return 2

        last_stage1: dict[str, Any] | None = None

        if "stage1_once" in want:
            msgs = stage1_messages(with_fewshot=True)
            r = await run_case(
                client,
                name="stage1_once",
                url=args.url,
                model=args.model,
                messages=msgs,
                options=options,
                think=args.think,
            )
            r["with_fewshot"] = True
            text = r.pop("_text", "")
            last_stage1 = parse_stage1_json(text)
            report["cases"]["stage1_once"] = r
            summary_rows.append(r)

        if "stage1_once_no_fewshot" in want:
            msgs = stage1_messages(with_fewshot=False)
            r = await run_case(
                client,
                name="stage1_once_no_fewshot",
                url=args.url,
                model=args.model,
                messages=msgs,
                options=options,
                think=args.think,
            )
            r["with_fewshot"] = False
            r.pop("_text", None)
            report["cases"]["stage1_once_no_fewshot"] = r
            summary_rows.append(r)

        if "stage1_x3" in want:
            r = await run_stage1_x3(
                client,
                url=args.url,
                model=args.model,
                options=options,
                think=args.think,
                with_fewshot=not args.no_fewshot_in_x3,
            )
            report["cases"]["stage1_x3"] = r
            summary_rows.append(r)

        if ("stage2_once" in want or "stage2_x3" in want) and last_stage1 is None:
            print("  [capture stage1 JSON for stage2] ...", flush=True)
            cap = await chat_timed(
                client,
                url=args.url,
                model=args.model,
                messages=stage1_messages(with_fewshot=True),
                options=options,
                think=args.think,
                store_text=True,
            )
            last_stage1 = parse_stage1_json(cap.get("text") or "")
            report["stage1_capture"] = {
                "wall_sec": cap.get("wall_sec"),
                "parsed_ok": last_stage1 is not None,
                "ollama": cap.get("ollama"),
            }

        if "stage2_once" in want:
            msgs = stage2_messages_from_stage1(last_stage1)
            r = await run_case(
                client,
                name="stage2_once",
                url=args.url,
                model=args.model,
                messages=msgs,
                options=options,
                think=args.think,
            )
            r.pop("_text", None)
            report["cases"]["stage2_once"] = r
            summary_rows.append(r)

        if "stage2_x3" in want:
            r = await run_stage2_x3(
                client,
                url=args.url,
                model=args.model,
                options=options,
                think=args.think,
                stage1=last_stage1,
            )
            report["cases"]["stage2_x3"] = r
            summary_rows.append(r)

    print_table(summary_rows)
    out = Path(args.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://192.168.53.10:11434")
    p.add_argument("--model", default="huihui_ai/gemma-4-abliterated:E4b-qat")
    p.add_argument("--think", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--num-ctx", type=int, default=32768)
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--timeout", type=float, default=900.0)
    p.add_argument(
        "--cases",
        nargs="+",
        default=["all"],
        help="all | stage1_once stage1_once_no_fewshot stage1_x3 stage2_once stage2_x3",
    )
    p.add_argument("--no-fewshot-in-x3", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--out",
        default=str(ROOT / "scripts" / "bench_chronicle_llm_report.json"),
    )
    return p


def main() -> int:
    args = build_argparser().parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
