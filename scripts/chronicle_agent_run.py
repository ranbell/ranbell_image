#!/usr/bin/env python3
"""Run Chronicle end-to-end via the agent orchestration API (no SSE).

Examples:
  python scripts/chronicle_agent_run.py --base-url http://192.168.53.10:3100 \\
    --api-token "$RANBELL_API_TOKEN" --catalog

  python scripts/chronicle_agent_run.py --base-url http://192.168.53.10:3100 \\
    --api-token "$RANBELL_API_TOKEN" --topic "雨の日の図書室" --use-catalog-defaults
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _req(
    method: str,
    url: str,
    body: dict | None = None,
    *,
    timeout: float = 60.0,
    api_token: str = "",
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    token = (api_token or "").strip()
    if token:
        headers["X-API-Token"] = token
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} {url}: {detail}") from exc


def _print_catalog_summary(cat: dict) -> None:
    sug = cat.get("suggested_run") or {}
    comfy = cat.get("comfyui") or {}
    llm = cat.get("llm") or {}
    ollama = llm.get("ollama") or {}
    openai = llm.get("openai") or {}
    authors = cat.get("authors") or []
    print("=== Chronicle catalog ===")
    print(f"comfyui ok={comfy.get('ok')} workflows={len(comfy.get('workflows') or [])}")
    for w in (comfy.get("workflows") or [])[:30]:
        print(f"  - {w}")
    if len(comfy.get("workflows") or []) > 30:
        print(f"  ... +{len(comfy['workflows']) - 30} more")
    print(f"ollama ok={ollama.get('ok')} models={len(ollama.get('models') or [])}")
    for m in (ollama.get("models") or [])[:20]:
        print(f"  - {m}")
    print(f"openai ok={openai.get('ok')} models={len(openai.get('models') or [])}")
    for m in (openai.get("models") or [])[:20]:
        print(f"  - {m}")
    print(f"authors={len(authors)}")
    for a in authors[:15]:
        print(f"  - {a.get('id')}: {a.get('name')} [{a.get('genre_tag') or '-'}]")
    print("--- suggested_run ---")
    print(json.dumps(sug, ensure_ascii=False, indent=2))
    print("--- notes ---")
    print(json.dumps(cat.get("notes") or {}, ensure_ascii=False, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description="Chronicle agent run (poll until done)")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument(
        "--api-token",
        default=os.environ.get("RANBELL_API_TOKEN", ""),
        help="X-API-Token (or env RANBELL_API_TOKEN)",
    )
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--catalog-json", action="store_true")
    ap.add_argument("--use-catalog-defaults", action="store_true")
    ap.add_argument("--topic", default="")
    ap.add_argument("--base-sha", default="", dest="base_sha256")
    ap.add_argument("--workflow", default="", dest="workflow_name")
    ap.add_argument("--candidate", default="A", dest="candidate_id")
    ap.add_argument("--story-model", default="", dest="story_model")
    ap.add_argument("--llm-provider", default="", dest="llm_provider")
    ap.add_argument("--author-style", default="")
    ap.add_argument("--author-id", default="")
    ap.add_argument("--locale", default="ja", choices=("ja", "en"))
    ap.add_argument("--time-scale", default="days")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--num-ctx", type=int, default=32768)
    ap.add_argument("--timeout-sec", type=float, default=1800.0)
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--no-wait-images", action="store_true")
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--export-dir", default="")
    ap.add_argument("--manual-mode", action="store_true")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    token = (args.api_token or "").strip()

    def req(method: str, url: str, body: dict | None = None, **kw: Any) -> Any:
        return _req(method, url, body, api_token=token, **kw)

    if args.catalog:
        cat = req("GET", f"{base}/api/story/chronicle/catalog", timeout=120.0)
        if args.catalog_json:
            print(json.dumps(cat, ensure_ascii=False, indent=2))
        else:
            _print_catalog_summary(cat if isinstance(cat, dict) else {})
        return 0

    if not (args.base_sha256 or "").strip() and not (args.topic or "").strip():
        ap.error("--topic is required when --base-sha is empty")

    workflow = args.workflow_name
    story_model = args.story_model
    llm_provider = args.llm_provider or "ollama"
    temperature = args.temperature
    num_ctx = args.num_ctx
    time_scale = args.time_scale
    locale = args.locale

    if args.use_catalog_defaults:
        cat = req("GET", f"{base}/api/story/chronicle/catalog", timeout=120.0)
        sug = (cat or {}).get("suggested_run") or {}
        if not workflow:
            workflow = sug.get("workflow_name") or ""
        if not story_model:
            story_model = sug.get("story_model") or ""
        if not args.llm_provider:
            llm_provider = sug.get("llm_provider") or "ollama"
        if args.temperature == 0.7 and sug.get("temperature") is not None:
            temperature = float(sug["temperature"])
        if args.num_ctx == 32768 and sug.get("num_ctx") is not None:
            num_ctx = int(sug["num_ctx"])
        if args.time_scale == "days" and sug.get("time_scale"):
            time_scale = str(sug["time_scale"])
        print(
            f"[chronicle_agent_run] catalog defaults → "
            f"workflow={workflow!r} story_model={story_model!r} "
            f"provider={llm_provider!r}",
            flush=True,
        )

    if not story_model:
        ap.error(
            "--story-model is required (Chronicle does not use Admin fallback). "
            "Run with --catalog or --use-catalog-defaults."
        )
    if not workflow and not args.manual_mode:
        print(
            "[chronicle_agent_run] warning: no --workflow; images will be skipped "
            "unless manual_mode",
            file=sys.stderr,
        )

    payload = {
        "base_sha256": args.base_sha256,
        "user_topic": args.topic,
        "workflow_name": workflow,
        "candidate_id": args.candidate_id,
        "story_model": story_model,
        "vlm_model": story_model,
        "llm_provider": llm_provider,
        "author_style": args.author_style,
        "author_id": args.author_id,
        "locale": locale,
        "time_scale": time_scale,
        "temperature": temperature,
        "num_ctx": num_ctx,
        "timeout_sec": args.timeout_sec,
        "wait_images": not args.no_wait_images,
        "export": not args.no_export,
        "export_dir": args.export_dir,
        "manual_mode": args.manual_mode,
    }
    print(f"[chronicle_agent_run] POST {base}/api/story/chronicle/run", flush=True)
    started = req("POST", f"{base}/api/story/chronicle/run", payload)
    if not isinstance(started, dict):
        print(started)
        return 1
    run_id = started.get("run_id")
    if not run_id:
        print(json.dumps(started, ensure_ascii=False, indent=2))
        return 1
    print(f"[chronicle_agent_run] run_id={run_id} status={started.get('status')}", flush=True)

    deadline = time.time() + float(args.timeout_sec) + 60.0
    last_status = None
    while time.time() < deadline:
        state = req("GET", f"{base}/api/story/chronicle/run/{run_id}", timeout=30.0)
        if not isinstance(state, dict):
            time.sleep(max(0.5, float(args.poll)))
            continue
        status = state.get("status")
        if status != last_status:
            print(
                f"[chronicle_agent_run] status={status}"
                f" story_id={state.get('story_id')}"
                f" export_dir={state.get('export_dir')}",
                flush=True,
            )
            last_status = status
        if status == "done":
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return 0
        if status == "error":
            print(json.dumps(state, ensure_ascii=False, indent=2), file=sys.stderr)
            return 2
        time.sleep(max(0.5, float(args.poll)))

    print(f"[chronicle_agent_run] timed out waiting for {run_id}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
