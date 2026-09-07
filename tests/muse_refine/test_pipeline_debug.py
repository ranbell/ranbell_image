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


def test_change_event_noop_does_not_spam_chat():
    from app.muse_refine import ledger as ledger_mod
    session = service.new_session({"locale": "ja"})
    session["session_id"] = "dbg-noop"
    led = {**ledger_mod.blank(), "wearing": "hoodie"}
    session["chat"] = []
    session["rewrite_log"] = []
    service._change_event(
        session,
        source="writer",
        patch={"wearing": "hoodie"},
        before=led,
        after=led,
        locale="ja",
    )
    assert session["rewrite_log"] == []
    assert not any((c.get("meta") or {}).get("kind") == "ledger_change" for c in session["chat"])


def test_lettering_records_rewrite_log():
    from app.muse_refine import anima as anima_mod
    from app.muse_refine import ledger as ledger_mod

    session = service.new_session({"locale": "ja"})
    session["session_id"] = "dbg-letter"
    session["refine_ledger"] = ledger_mod.blank()
    phrases, _ = anima_mod.extract_lettering('看板に「OPEN」と書いて')
    assert phrases
    before = dict(session["refine_ledger"])
    after = {**before, "lettering": phrases[0]}
    session["refine_ledger"] = after
    service._change_event(
        session,
        source="lettering",
        patch={"lettering": phrases[0]},
        before=before,
        after=after,
        locale="ja",
    )
    assert session["refine_ledger"]["lettering"] == phrases[0]
    assert any(
        "lettering" in (e.get("changed") or {})
        for e in (session.get("rewrite_log") or [])
    )


def test_pipeline_marks_ok_from_rewrite_log_alone():
    session = service.new_session()
    session["rewrite_log"] = [{
        "at": 1,
        "source": "restate",
        "intent": "restate",
        "changed": {"beat": {"before": "", "after": "standing"}},
    }]
    pipe = pipeline_view.build_pipeline_view(session)
    by_id = {s["id"]: s for s in pipe["stages"]}
    assert by_id["writer"]["status"] == "ok"
    assert by_id["ledger"]["status"] == "ok"


def test_pipeline_and_public_view_expose_visible_consequences():
    from app.muse_refine import ledger as ledger_mod

    session = service.new_session({"locale": "ja"})
    session["session_id"] = "dbg-vc"
    session["refine_ledger"] = {
        **ledger_mod.blank(),
        "beat": "sitting, holding a cup, looking down",
        "expression": "tears welling",
        "wearing": "hoodie",
        "scene": "cafe",
    }
    session["craft"] = {
        "prompt": "1girl, hoodie, sitting, holding, looking_down, tearing_up",
        "visible_consequences": {
            "causes": ["sitting", "holding", "looking_down", "tears"],
            "tags": ["sitting", "holding", "looking_down", "tearing_up"],
            "hints": ["Seated weight settles through hips and thighs."],
            "densified": False,
            "densify_reason": "",
            "craft_only": True,
        },
    }
    pipe = pipeline_view.build_pipeline_view(session)
    vc = pipe["visible_consequences"]
    assert "sitting" in vc["causes"]
    assert "tearing_up" in vc["tags"]
    assert vc["craft_only"] is True
    assemble = next(s for s in pipe["stages"] if s["id"] == "assemble")
    assert "holding" in assemble["visible_tags"]
    view = service.public_view(session)
    assert view["craft"]["visible_consequences"]["causes"] == vc["causes"]


def test_visible_consequence_causes_for_debug():
    from app.muse_refine import assemble, ledger as ledger_mod

    cues = assemble.visible_consequence_cues({
        **ledger_mod.blank(),
        "beat": "arms raised overhead",
        "light": "backlight",
    })
    assert "arms_up" in cues["causes"]
    assert "backlight" in cues["causes"]
    assert "arms_up" in cues["tags"]
    assert "rim_light" in cues["tags"]
