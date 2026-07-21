"""Build Chronicle eval bundles and export them for agent review."""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Keep in sync with story.db.AXES (avoid importing db → qdrant in unit tests).
AXES = ("panel_1", "panel_2", "panel_3")


def repo_root() -> Path:
    # backend/app/story/eval_export.py → parents[3] = repo root
    return Path(__file__).resolve().parents[3]


def default_eval_root() -> Path:
    return repo_root() / "chronicle_evals"


def build_eval_bundle(story: dict[str, Any], *, db_docs: dict[str, dict] | None = None) -> dict[str, Any]:
    """JSON-serialisable eval pack (no binary). ``db_docs`` maps sha → image doc."""
    story_id = str(story.get("story_id") or "")
    axes_out: dict[str, Any] = {}
    docs = db_docs or {}
    for axis in AXES:
        ax = (story.get("axes") or {}).get(axis) or {}
        sha = ax.get("image_id") or ""
        doc = docs.get(sha) or {}
        axes_out[axis] = {
            "story": ax.get("story") or "",
            "story_ja": ax.get("story_ja") or "",
            "prompt_positive": ax.get("prompt_positive") or "",
            "prompt_negative": ax.get("prompt_negative") or "",
            "visual_script": ax.get("visual_script") or "",
            "camera": ax.get("camera") or "",
            "character_state_diff": ax.get("character_state_diff") or "",
            "image_id": sha or None,
            "image_path": doc.get("path") or None,
            "thumbnail_url": f"/api/thumbnails/{sha}.webp" if sha else None,
            "original_url": f"/api/originals/{sha}" if sha else None,
        }
    return {
        "story_id": story_id,
        "status": story.get("status"),
        "title": story.get("title") or "",
        "overall_story": story.get("overall_story") or "",
        "user_topic": story.get("user_topic") or "",
        "author_style": story.get("author_style") or "",
        "time_scale": story.get("time_scale") or "",
        "workflow_name": story.get("workflow_name") or "",
        "base_image_id": story.get("base_image_id") or "",
        "selected_candidate": story.get("selected_candidate") or "",
        "quality_eval": story.get("quality_eval"),
        "axes": axes_out,
        "group_id": story.get("group_id") or "",
        "created_at": story.get("created_at"),
    }


def _prompts_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        f"# Chronicle prompts — {bundle.get('story_id')}",
        "",
        f"**Title:** {bundle.get('title') or '(none)'}",
        f"**Topic:** {bundle.get('user_topic') or '(none)'}",
        "",
    ]
    for axis in AXES:
        ax = (bundle.get("axes") or {}).get(axis) or {}
        lines.append(f"## {axis}")
        lines.append("")
        lines.append(f"- narrative: {ax.get('story_ja') or ax.get('story') or ''}")
        lines.append(f"- image_id: {ax.get('image_id') or '(none)'}")
        lines.append("")
        lines.append("### positive")
        lines.append("```")
        lines.append(ax.get("prompt_positive") or "")
        lines.append("```")
        lines.append("")
        lines.append("### negative")
        lines.append("```")
        lines.append(ax.get("prompt_negative") or "")
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def export_eval_bundle(
    story: dict[str, Any],
    *,
    db_docs: dict[str, dict] | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Write report.json, prompts.md, and panel_*.png copies. Returns export meta."""
    story_id = str(story.get("story_id") or "").strip()
    if not story_id:
        raise ValueError("story_id required")

    root = Path(out_dir) if out_dir else default_eval_root() / story_id
    root.mkdir(parents=True, exist_ok=True)

    bundle = build_eval_bundle(story, db_docs=db_docs)
    report_path = root / "report.json"
    report_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "prompts.md").write_text(_prompts_markdown(bundle), encoding="utf-8")

    copied: dict[str, str] = {}
    missing: list[str] = []
    docs = db_docs or {}
    for axis in AXES:
        ax = (bundle.get("axes") or {}).get(axis) or {}
        sha = ax.get("image_id") or ""
        if not sha:
            missing.append(axis)
            continue
        src = Path(str((docs.get(sha) or {}).get("path") or ax.get("image_path") or ""))
        if not src.is_file():
            missing.append(axis)
            logger.warning("[eval_export] missing file for %s sha=%s path=%s", axis, sha[:12], src)
            continue
        dest = root / f"{axis}{src.suffix or '.png'}"
        shutil.copy2(src, dest)
        copied[axis] = str(dest)

    meta = {
        "story_id": story_id,
        "export_dir": str(root.resolve()),
        "report_path": str(report_path.resolve()),
        "copied_panels": copied,
        "missing_panels": missing,
        "bundle": bundle,
    }
    (root / "export_meta.json").write_text(
        json.dumps({k: v for k, v in meta.items() if k != "bundle"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta
