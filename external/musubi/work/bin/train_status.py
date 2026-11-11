#!/usr/bin/env python3
"""How a training run is going, in the terminal.

    docker compose exec musubi status                 # latest run
    docker compose exec musubi status my_style       # latest run of that dataset
    docker compose exec musubi status --list          # every run
    docker compose exec musubi status -w 60           # refresh every 60 s (Ctrl-C to leave)

Shows the progress line (step, s/step, time left), a per-epoch table (mean /
min / max loss, saved file) and the per-step loss as a small chart, smoothed.

Diffusion loss is noisy and drops little; read it for trouble (a jump, NaN,
a run that stopped), not for "is it good yet" — that is what the saved
epochs are for.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

OUTPUT = Path(os.environ.get("MUSUBI_WORK", "/work")) / "output"
STALE_AFTER = 600  # seconds without a log write → the run is not alive
_PROGRESS = re.compile(r"(\d+)/(\d+) \[([\d:]+)<([\d:?]+),\s*([\d.]+)(s/it|it/s)")


def runs() -> list[Path]:
    return sorted((p for p in OUTPUT.glob("*-*") if (p / "train.log").exists()),
                  key=lambda p: p.stat().st_mtime)


def pick(name: str | None) -> Path | None:
    found = [r for r in runs() if name is None or r.name == name or r.name.rsplit("-", 2)[0] == name]
    return found[-1] if found else None


def flag(cmd: str, name: str, default: str = "") -> str:
    m = re.search(rf"--{name} (\S+)", cmd)
    return m.group(1) if m else default


def read_log(run: Path) -> dict:
    text = (run / "train.log").read_text(encoding="utf-8", errors="replace")
    trains = [ln for ln in text.splitlines() if ln.startswith("=== train:")]
    cmd = trains[-1] if trains else ""
    last = None
    for m in _PROGRESS.finditer(text):
        last = m
    info = {
        "cmd": cmd,
        "epochs": int(flag(cmd, "max_train_epochs", "0") or 0),
        "swap": flag(cmd, "blocks_to_swap", "0"),
        "rank": flag(cmd, "network_dim", "?"),
        "lr": flag(cmd, "learning_rate", "?"),
        "retries": text.count("out of VRAM with blocks_to_swap"),
        "oom": "OutOfMemoryError" in text,
    }
    if last:
        step, total, elapsed, left, rate, unit = last.groups()
        sec = float(rate) if unit == "s/it" else (1 / float(rate) if float(rate) else 0)
        info.update(step=int(step), total=int(total), elapsed=elapsed, left=left, sec_per_step=sec)
    return info


def read_scalars(run: Path) -> tuple[list[tuple[int, float]], dict[int, float], list[float]]:
    """Per-step loss, per-epoch loss, and the wall time of each logged step."""
    files = sorted(glob.glob(str(run / "logs" / "**" / "events*"), recursive=True))
    if not files:
        return [], {}, []
    from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

    steps: dict[int, float] = {}
    epochs: dict[int, float] = {}
    walls: dict[int, float] = {}
    for f in files:  # a retried run writes a new file; the last one wins per step
        ea = EventAccumulator(f, size_guidance={"scalars": 0})
        ea.Reload()
        tags = ea.Tags().get("scalars", [])
        if "loss/current" in tags:
            for e in ea.Scalars("loss/current"):
                steps[e.step] = e.value
                walls[e.step] = e.wall_time
        if "loss/epoch" in tags:
            epochs.update({e.step: e.value for e in ea.Scalars("loss/epoch")})
    return sorted(steps.items()), epochs, [walls[k] for k in sorted(walls)]


def _hms(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def state_of(run: Path) -> str:
    summary = run / "summary.json"
    if summary.exists():
        return json.loads(summary.read_text()).get("status", "?")
    # The loss events are written every step; the log can lag behind them.
    files = [run / "train.log", *map(Path, glob.glob(str(run / "logs" / "**" / "events*"), recursive=True))]
    age = time.time() - max(f.stat().st_mtime for f in files)
    return "running" if age < STALE_AFTER else f"stopped? (nothing written for {int(age // 60)} min)"


def chart(points: list[tuple[int, float]], width: int = 60, height: int = 10) -> list[str]:
    """Per-step loss, averaged into `width` columns."""
    if len(points) < 2:
        return ["  (not enough steps yet)"]
    vals = [v for _, v in points]
    cols: list[float] = []
    n = len(vals)
    for c in range(min(width, n)):
        a, b = c * n // min(width, n), (c + 1) * n // min(width, n)
        chunk = vals[a:b] or [vals[a]]
        cols.append(sum(chunk) / len(chunk))
    lo, hi = min(cols), max(cols)
    span = (hi - lo) or 1e-9
    rows = []
    for r in range(height, 0, -1):
        cut = lo + span * (r - 0.5) / height
        label = f"{lo + span * r / height:7.4f} ┤" if r in (height, 1) or r == (height + 1) // 2 else "        │"
        rows.append(label + "".join("█" if v >= cut else " " for v in cols))
    rows.append("        └" + "─" * len(cols))
    rows.append(f"         step 1{' ' * max(1, len(cols) - 6 - len(str(points[-1][0])))}{points[-1][0]}")
    return rows


def report(run: Path) -> str:
    log = read_log(run)
    steps, epoch_loss, walls = read_scalars(run)
    meta_path = run / "dataset_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    images = meta.get("count") or len(meta.get("items", [])) or 0
    repeats = int((re.search(r"num_repeats = (\d+)", (run / "dataset.toml").read_text()) or [0, 1])[1]) \
        if (run / "dataset.toml").exists() else 1
    per_epoch = max(1, images * repeats)
    name = flag(log["cmd"], "output_name", run.name)
    saved = {p.name for p in run.glob("*.safetensors")}

    out = [f"run      {run.name}   [{state_of(run)}]",
           f"dataset  {meta.get('name', '?')}  {images} pictures × {repeats}, trigger {meta.get('trigger', '')!r}",
           f"setting  rank {log['rank']}, lr {log['lr']}, swap {log['swap']}, {log['epochs']} epochs"
           + (f"   (OOM retries: {log['retries']})" if log["retries"] else "")]
    total = log["epochs"] * per_epoch
    if steps and len(walls) >= 2 and (not log.get("step") or steps[-1][0] > log["step"]):
        # The log can lag (or be from an older wrapper); the loss events carry
        # their own wall time, so the rate and the time left come from them.
        recent = walls[-min(len(walls), 20):]
        sec = (recent[-1] - recent[0]) / max(1, len(recent) - 1)
        done = steps[-1][0]
        live = state_of(run) == "running"
        log.update(step=done, total=total or done, sec_per_step=sec,
                   elapsed=_hms(walls[-1] - walls[0]),
                   left=_hms(max(0, (total - done) * sec)) if live else "-")
    if "step" in log:
        pct = log["step"] / log["total"] * 100 if log["total"] else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        out.append(f"progress {bar} {log['step']}/{log['total']} ({pct:.0f}%)  "
                   f"{log['sec_per_step']:.1f} s/step, elapsed {log['elapsed']}, left {log['left']}")
    out.append("")

    if steps:
        lows: list[str] = []
        out.append("epoch   mean     min      max      saved")
        last_epoch = max(epoch_loss) if epoch_loss else 0
        current = (steps[-1][0] - 1) // per_epoch + 1
        means = [epoch_loss[e] for e in sorted(epoch_loss)]
        best = min(means) if means else None
        for e in range(1, max(last_epoch, current) + 1):
            chunk = [v for s, v in steps if (s - 1) // per_epoch + 1 == e]
            if not chunk:
                continue
            mean = epoch_loss.get(e, sum(chunk) / len(chunk))
            file = f"{name}-{e:06d}.safetensors"
            mark = file if file in saved else ("(final)" if e == log["epochs"] and f"{name}.safetensors" in saved else "")
            note = "  (lowest loss — not \"best\")" if best is not None and e in epoch_loss and mean == best and len(means) > 2 else ""
            running = "" if e in epoch_loss else " …"
            out.append(f"{e:5d}   {mean:.4f}   {min(chunk):.4f}   {max(chunk):.4f}   {mark}{note}{running}")
        out.append("")
        out.append("loss per step (smoothed)")
        out.extend(chart(steps))
        bad = [s for s, v in steps if v != v or v > 1.0]
        if bad:
            out.append(f"\n⚠ odd loss values (NaN or > 1.0) at steps {bad[:5]}")
    else:
        out.append("(no loss logged yet — still caching or loading the model)")
    if log["oom"] and state_of(run) not in ("done", "running"):
        out.append("\n⚠ ran out of VRAM — try --blocks-to-swap 24 or --preset 16gb-safe")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="status", description="Show how a training run is going.")
    ap.add_argument("run", nargs="?", help="run folder name, or a dataset name for its latest run")
    ap.add_argument("--list", action="store_true", help="list every run")
    ap.add_argument("-w", "--watch", type=int, metavar="SEC", help="refresh every SEC seconds")
    args = ap.parse_args(argv)

    if args.list:
        for r in runs():
            print(f"{r.name:40s} {state_of(r)}")
        return 0
    run = pick(args.run)
    if run is None:
        print("no runs found" + (f" for {args.run!r}" if args.run else ""), file=sys.stderr)
        return 1
    if not args.watch:
        print(report(run))
        return 0
    try:
        while True:
            text = report(run)
            sys.stdout.write("\033[2J\033[H" + text + f"\n\n(refreshing every {args.watch}s — Ctrl-C to leave)\n")
            sys.stdout.flush()
            time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
