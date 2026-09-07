"""Refine debug API parity with classic Muse pipeline / rewrite_log."""
from app.muse_refine import debug as debug_mod
from app.muse_refine import pipeline_view, service


def test_pipeline_view_schema_and_stages():
    from app.muse_refine import ledger as ledger_mod
    session = service.new_session({"locale": "ja"})
    session["session_id"] = "dbg-1"
    session["refine_ledger"] = {
        **ledger_mod.blank(),
        "wearing": "white shirt",
        "scene": "rooftop at dusk",
    }
    session["craft"] = {"prompt": "1girl, white shirt, masterpiece"}
    session["turn_trace"] = [{
        "at": 1,
        "line": "白いシャツで屋上",
        "patch": {"wearing": "white shirt", "scene": "rooftop at dusk"},
        "propose": {},
        "moved": {"wearing": "'' → 'white shirt'"},
    }]
    session["refine_log"] = [
        {"at": 1, "kind": "writer_patch", "detail": "{}", "patch": {"wearing": "white shirt"}},
        {"at": 2, "kind": "actress", "detail": "うん"},
        {"at": 3, "kind": "verify", "detail": "ok", "ok": True},
    ]
    pipe = pipeline_view.build_pipeline_view(session)
    assert pipe["schema"] == pipeline_view.PIPELINE_SCHEMA
    ids = [s["id"] for s in pipe["stages"]]
    assert ids == list(pipeline_view._STAGE_IDS)
    by_id = {s["id"]: s for s in pipe["stages"]}
    assert by_id["writer"]["status"] == "ok"
    assert by_id["ledger"]["status"] == "ok"
    assert by_id["assemble"]["status"] == "ok"
    # scene tokens missing from prompt → divergence
    assert any(d["field"] == "scene" for d in pipe["divergences"])


def test_record_rewrite_and_public_view():
    session = service.new_session()
    session["session_id"] = "dbg-2"
    before = {"wearing": "", "scene": ""}
    after = {"wearing": "hoodie", "scene": "park"}
    entry = debug_mod.record_rewrite(
        session, "director", before=before, after=after, intent="director",
    )
    assert entry is not None
    assert entry["changed"]["wearing"]["after"] == "hoodie"
    assert len(session["rewrite_log"]) == 1
    view = service.public_view(session)
    assert view["rewrite_log"]
    assert view["pipeline"]["schema"] == pipeline_view.PIPELINE_SCHEMA


def test_change_event_writes_rewrite_log():
    from app.muse_refine import ledger as ledger_mod
    session = service.new_session({"locale": "ja"})
    session["session_id"] = "dbg-3"
    before = {**ledger_mod.blank(), "wearing": ""}
    after = {**ledger_mod.blank(), "wearing": "red dress"}
    service._change_event(
        session,
        source="writer",
        patch={"wearing": "red dress"},
        before=before,
        after=after,
        locale="ja",
    )
    assert session["rewrite_log"]
    assert session["rewrite_log"][-1]["changed"]["wearing"]["after"] == "red dress"
