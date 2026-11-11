#!/usr/bin/env python3
"""Make a copy of a LoRA that is fit to publish (Civitai etc.).

    docker compose exec musubi publish flt_style                # final LoRA of the latest flt_style run
    docker compose exec musubi publish flt_style --epoch 20     # that epoch instead
    docker compose exec musubi publish /work/output/<run>/<file>.safetensors
    docker compose exec musubi publish flt_style --all          # drop every metadata field

The weights are copied byte for byte; only the safetensors header's
``__metadata__`` changes. By default the training settings stay (rank, lr,
epochs… — useful to whoever uses the LoRA) and what says when, where and
from which files it was trained goes:

  - times: ss_training_started_at / ss_training_finished_at / modelspec.date
  - ss_session_id
  - file names: ss_sd_model_name, ss_vae_name, the dataset folder in ss_datasets
  - anything caption-derived, if a trainer ever wrote it (ss_tag_frequency,
    ss_dataset_dirs, ss_training_comment)

musubi-tuner does not store captions or tags in the LoRA; this is a guard in
case another trainer's file is passed in.

Writes <run>/publish/<name>.safetensors and prints what was removed.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import sys
from pathlib import Path

OUTPUT = Path(os.environ.get("MUSUBI_WORK", "/work")) / "output"

DROP = {
    "ss_training_started_at", "ss_training_finished_at", "modelspec.date",
    "ss_session_id", "ss_sd_model_name", "ss_vae_name",
    "ss_tag_frequency", "ss_dataset_dirs", "ss_training_comment",
    "ss_new_sd_model_hash", "ss_sd_model_hash",
}
# Fields whose value is rewritten rather than dropped.
SCRUB_DATASET_KEYS = ("image_directory", "cache_directory", "video_directory",
                      "control_directory", "image_jsonl_file", "video_jsonl_file")


def read_header(path: Path) -> tuple[dict, int]:
    """(header JSON, byte offset where tensor data starts)."""
    with path.open("rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    return header, 8 + n


def clean_metadata(meta: dict[str, str], *, drop_all: bool) -> tuple[dict[str, str], list[str]]:
    if drop_all:
        return {}, sorted(meta)
    out: dict[str, str] = {}
    removed: list[str] = []
    for k, v in meta.items():
        if k in DROP:
            removed.append(k)
            continue
        if k == "ss_datasets":
            try:
                ds = json.loads(v)
                changed = False
                for d in ds if isinstance(ds, list) else []:
                    for key in SCRUB_DATASET_KEYS:
                        if key in d:
                            d.pop(key)
                            changed = True
                if changed:
                    removed.append("ss_datasets: folder names")
                v = json.dumps(ds)
            except ValueError:
                removed.append(k)
                continue
        out[k] = v
    return out, sorted(removed)


def write_with_metadata(src: Path, dst: Path, meta: dict[str, str]) -> None:
    header, data_start = read_header(src)
    header.pop("__metadata__", None)
    if meta:
        header = {"__metadata__": meta, **header}
    raw = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    raw += b" " * (-len(raw) % 8)  # keep the data 8-byte aligned, as safetensors writers do
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with src.open("rb") as fi, tmp.open("wb") as fo:
        fo.write(struct.pack("<Q", len(raw)))
        fo.write(raw)
        fi.seek(data_start)
        shutil.copyfileobj(fi, fo, 16 * 1024 * 1024)
    os.replace(tmp, dst)


def resolve(target: str, epoch: int | None) -> Path:
    p = Path(target)
    if p.suffix == ".safetensors":
        if not p.is_file():
            raise SystemExit(f"✋ not found: {p}")
        return p
    runs = sorted((r for r in OUTPUT.glob("*-*") if r.name == target or r.name.rsplit("-", 2)[0] == target),
                  key=lambda r: r.stat().st_mtime)
    if not runs:
        raise SystemExit(f"✋ no run found for {target!r}")
    run = runs[-1]
    name = run.name.rsplit("-", 2)[0]
    files = {f.name: f for f in run.glob("*.safetensors")}
    want = f"{name}-{epoch:06d}.safetensors" if epoch else f"{name}.safetensors"
    if want not in files:
        have = ", ".join(sorted(files)) or "none yet"
        raise SystemExit(f"✋ {want} is not in {run.name} (have: {have})")
    return files[want]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="publish", description="Copy a LoRA with its metadata cleaned for publishing.")
    ap.add_argument("target", help="dataset/run name, or a .safetensors path")
    ap.add_argument("--epoch", type=int, help="pick that epoch's file instead of the final one")
    ap.add_argument("--all", dest="drop_all", action="store_true", help="drop every metadata field")
    ap.add_argument("-o", "--output", help="output file (default: <run>/publish/<file>)")
    args = ap.parse_args(argv)

    src = resolve(args.target, args.epoch)
    header, _ = read_header(src)
    meta, removed = clean_metadata(header.get("__metadata__", {}), drop_all=args.drop_all)

    dst = Path(args.output) if args.output else src.parent / "publish" / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    write_with_metadata(src, dst, meta)
    try:  # the container runs as root; give it to whoever owns the run folder
        st = src.parent.stat()
        for p in (dst.parent, dst):
            os.chown(p, st.st_uid, st.st_gid)
        os.chmod(dst, 0o664)
    except OSError:
        pass

    print(f"{src}\n  → {dst}")
    print("removed: " + (", ".join(removed) if removed else "(nothing)"))
    print(f"kept:    {len(meta)} fields" + (" (training settings)" if meta else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
