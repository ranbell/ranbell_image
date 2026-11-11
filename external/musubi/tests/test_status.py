"""The status view's pieces that need no tensorboard."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "work" / "bin" / "train_status.py"


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSUBI_WORK", str(tmp_path))
    spec = importlib.util.spec_from_file_location("train_status", BIN)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "train_status", mod)
    spec.loader.exec_module(mod)
    (tmp_path / "output").mkdir()
    return mod


def _run(st, name, log=""):
    d = st.OUTPUT / name
    d.mkdir()
    (d / "train.log").write_text(log)
    return d


def test_dataset_name_picks_its_latest_run(st):
    import os, time
    a = _run(st, "my_style-20261111-0035")
    b = _run(st, "my_style-20261111-0040")
    _run(st, "other-20261111-0100")
    os.utime(a, (time.time() - 100,) * 2)
    assert st.pick("my_style") == b
    assert st.pick("my_style-20261111-0035") == a


def test_progress_and_settings_come_from_the_log(st):
    run = _run(st, "x-20261111-0000",
               "=== train: accelerate ... --max_train_epochs 30 --blocks_to_swap 20 --network_dim 32\n"
               "steps:   3%|▎ | 18/540 [04:16<2:11:11, 15.08s/it, avr_loss=0.112]\n")
    log = st.read_log(run)
    assert (log["epochs"], log["swap"], log["rank"]) == (30, "20", "32")
    assert (log["step"], log["total"], log["sec_per_step"]) == (18, 540, 15.08)


def test_finished_run_reports_its_summary_status(st):
    run = _run(st, "x-20261111-0000")
    (run / "summary.json").write_text('{"status": "done"}')
    assert st.state_of(run) == "done"


def test_chart_has_one_column_per_bucket_and_labels(st):
    rows = st.chart([(i, 0.1 + (i % 5) / 100) for i in range(1, 121)], width=40, height=6)
    assert len(rows) == 6 + 2
    assert all(len(r) == len(rows[0]) for r in rows[:6])
    assert "120" in rows[-1]


def test_a_quiet_log_with_fresh_loss_events_is_still_running(st):
    import os, time
    run = _run(st, "x-20261111-0000")
    old = time.time() - 3600
    os.utime(run / "train.log", (old, old))
    ev = run / "logs" / "1" / "network_train"
    ev.mkdir(parents=True)
    (ev / "events.out.tfevents.1").write_bytes(b"")
    assert st.state_of(run) == "running"
    os.utime(ev / "events.out.tfevents.1", (old, old))
    assert st.state_of(run).startswith("stopped?")
