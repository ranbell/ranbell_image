"""Evaluate Chronicle prompts for expression / diversity (rule-based + optional VLM).

Rule-based checks always run (no services). With ``--ollama`` and a reachable
Ollama host, a small VLM (Gemma 4 / Gemma3 / etc.) scores each axis prompt on a
short rubric — text-only, so it works without generated images.

Examples:
  PYTHONPATH=backend python3 tests/story/eval_chronicle_expression.py
  PYTHONPATH=backend python3 tests/story/eval_chronicle_expression.py \\
      --ollama --model gemma3:4b
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import (  # noqa: E402
    AXES,
    _chronicle_tags_degenerate,
    _tag_has_expression,
    _tag_has_person_subject,
    axis_tag_lines_collapsed,
)

# Fixture prompts: good vs missing-expression (mirrors diversity sim findings).
FIXTURES = {
    "good_cafe": {
        "past": (
            "1girl, spilling, holding, surprised, milk, pitcher, apron, cafe, "
            "morning, towel, indoors, counter, silver_hair, blue_eyes, solo, "
            "reaching, wet, day, daylight, detailed_background, depth_of_field, "
            "cinematic_lighting, highres, sharp_focus, dynamic_angle, steam, "
            "white_shirt, open_mouth, foam, metal_pitcher"
        ),
        "present": (
            "1girl, pouring, holding, smile, latte_art, coffee_cup, cafe, day, "
            "window, indoors, counter, steam, ceramic, silver_hair, blue_eyes, "
            "solo, concentrating, daylight, detailed_background, depth_of_field, "
            "cinematic_lighting, highres, sharp_focus, dynamic_angle, apron, "
            "heart, warm_light"
        ),
        "future": (
            "1girl, wiping, pointing, teaching, serious, espresso_machine, cafe, "
            "indoors, evening, cloth, steam, back_bar, warm_light, silver_hair, "
            "blue_eyes, solo, detailed_background, depth_of_field, "
            "cinematic_lighting, highres, sharp_focus, dynamic_angle, apron, "
            "junior, steamer_wand"
        ),
    },
    "no_expression": {
        "past": (
            "1girl, spilling, holding, milk, pitcher, apron, cafe, morning, "
            "towel, indoors, counter, silver_hair, blue_eyes, solo, reaching, "
            "wet, day, daylight, detailed_background, depth_of_field, "
            "cinematic_lighting, highres, sharp_focus, dynamic_angle, steam, "
            "white_shirt, foam, metal_pitcher"
        ),
        "present": (
            "1girl, pouring, holding, latte_art, coffee_cup, cafe, day, window, "
            "indoors, counter, steam, ceramic, silver_hair, blue_eyes, solo, "
            "daylight, detailed_background, depth_of_field, cinematic_lighting, "
            "highres, sharp_focus, dynamic_angle, apron, heart, warm_light"
        ),
        "future": (
            "1girl, wiping, pointing, teaching, espresso_machine, cafe, indoors, "
            "evening, cloth, steam, back_bar, warm_light, silver_hair, blue_eyes, "
            "solo, detailed_background, depth_of_field, cinematic_lighting, "
            "highres, sharp_focus, dynamic_angle, apron, junior, steamer_wand, "
            "barista"
        ),
    },
}


def rule_score(prompts: dict[str, str]) -> dict:
    per_axis = {}
    for a in AXES:
        parts = [t.strip() for t in (prompts.get(a) or "").split(",") if t.strip()]
        deg, reason = _chronicle_tags_degenerate(prompts.get(a) or "")
        per_axis[a] = {
            "person": _tag_has_person_subject(parts),
            "has_expression": _tag_has_expression(parts),
            "degenerate": deg,
            "reason": reason or "ok",
            "tag_count": len(parts),
        }
    return {
        "per_axis": per_axis,
        "all_have_expression": all(
            (not v["person"]) or v["has_expression"] for v in per_axis.values()
        ),
        "any_degenerate": any(v["degenerate"] for v in per_axis.values()),
        "tag_lines_collapsed": axis_tag_lines_collapsed(prompts),
    }


def _ollama_generate(model: str, prompt: str, *, host: str, timeout: int = 120) -> str:
    url = host.rstrip("/") + "/api/generate"
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_predict": 400},
    }).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return str(data.get("response") or "")


_VLM_RUBRIC = """You are grading anime image prompts for emotional readability.
For EACH axis (past/present/future), score 0-2:
  expression (0=missing face mood, 1=weak/generic, 2=clear specific expression)
  action (0=idle standing, 1=weak, 2=concrete physical action)
  emotion_legible (0=cannot feel mood, 2=mood reads from tags alone)
Also give cross_axis_diversity 0-2 (0=same shot thrice, 2=clearly different moments).

Prompts:
{prompts_json}

Return JSON only:
{{"past":{{"expression":0,"action":0,"emotion_legible":0,"note":"..."}},
 "present":{{...}},"future":{{...}},
 "cross_axis_diversity":0,"summary":"one sentence"}}
"""


def vlm_score(prompts: dict[str, str], *, model: str, host: str) -> dict | None:
    raw = _ollama_generate(
        model,
        _VLM_RUBRIC.format(prompts_json=json.dumps(prompts, ensure_ascii=False, indent=2)),
        host=host,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw, "parse_error": True}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ollama", action="store_true", help="Also score with Ollama VLM")
    ap.add_argument("--model", default="gemma3:4b", help="Ollama model name")
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--fixture", choices=[*FIXTURES, "all"], default="all")
    args = ap.parse_args()

    names = list(FIXTURES) if args.fixture == "all" else [args.fixture]
    exit_code = 0
    for name in names:
        prompts = FIXTURES[name]
        rules = rule_score(prompts)
        print(f"\n═══ {name} ═══")
        print(json.dumps(rules, ensure_ascii=False, indent=2))
        if name == "good_cafe":
            if not rules["all_have_expression"] or rules["any_degenerate"]:
                print("FAIL: good fixture should pass expression/action gates")
                exit_code = 1
        if name == "no_expression":
            if rules["all_have_expression"]:
                print("FAIL: no_expression fixture should lack expressions")
                exit_code = 1
            if not any(
                v["reason"] == "no_expression" for v in rules["per_axis"].values()
            ):
                print("FAIL: expected no_expression degeneration reason")
                exit_code = 1

        if args.ollama:
            try:
                vlm = vlm_score(prompts, model=args.model, host=args.host)
                print(f"VLM({args.model}):")
                print(json.dumps(vlm, ensure_ascii=False, indent=2))
            except urllib.error.URLError as exc:
                print(f"VLM skipped — Ollama unreachable: {exc}")
                print(
                    "  Tip: start Ollama and pull a small multimodal/text model, e.g.\n"
                    "    ollama pull gemma3:4b   # or gemma4 when available locally"
                )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
