"""API-level e2e for /api/weave.

Drives the real FastAPI router over ASGI: api.py, session_db.py (Qdrant round
trip), service, compile, gates and render attach all run for real. Only the
process boundaries are faked — see weave_fakes.py.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from weave_fakes import FakeDb, FakeLLM, build_app, client_for, story_json

WIDE_TAGS = ["full_body", "scenery", "bookshelf", "indoors", "standing"]


def run(coro):
    return asyncio.run(coro)


async def _create(client, **over):
    body = {
        "topic": "雨の日の小さな書店",
        "personality_text": "慎重で皮肉屋。困っている客は放っておけない古書店の店員。",
        "author_style": "静かな筆致の日常小説",
        "story_model": "test-model",
        "workflow_final": "weave_test.json",
        **over,
    }
    resp = await client.post("/api/weave/sessions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _attach(db, session_id, *, kind, target, image_id, job_id="", seed_index=0,
                  wd14=None):
    """Simulate the GEN-lane runner finishing an image."""
    from app.weave import session_db

    db.add_image(image_id, wd14_tags=wd14 or [])
    await session_db.attach_render_result(
        db, session_id, kind=kind, target=target, image_id=image_id,
        job_id=job_id, seed_index=seed_index, ollama=None,
    )


async def _lock_and_story(client, sid):
    r = await client.post(f"/api/weave/sessions/{sid}/character/infer", json={})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/weave/sessions/{sid}/character/lock")
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/weave/sessions/{sid}/story/generate", json={})
    assert r.status_code == 200, r.text
    return r.json()


# ── Happy path ────────────────────────────────────────────────────────────────
def test_full_session_character_to_seal():
    async def _run():
        llm = FakeLLM()
        db = FakeDb()
        app = build_app(llm=llm, db=db)
        async with client_for(app) as client:
            view = await _create(client)
            sid = view["session_id"]
            assert view["status"] == "character"
            assert view["next_cta"]["code"] == "infer_character"

            # ── character ────────────────────────────────────────────────────
            r = await client.post(f"/api/weave/sessions/{sid}/character/infer", json={})
            assert r.status_code == 200, r.text
            ch = r.json()["character"]
            # P5: prop must not survive inside identity, even though the LLM
            # returned cloth_bookmark in identity_tags.
            assert "cloth_bookmark" not in ch["identity_tags"]
            assert "cloth_bookmark" in ch["prop_tags"]
            assert ch["signature_prop"] == "cloth_bookmark"
            assert ch["identity_locked"] is False
            assert r.json()["next_cta"]["code"] == "lock_identity"

            # Story is refused before G0-soft.
            r = await client.post(f"/api/weave/sessions/{sid}/story/generate", json={})
            assert r.status_code == 400
            assert "locked" in r.json()["detail"]

            r = await client.post(f"/api/weave/sessions/{sid}/character/lock")
            assert r.status_code == 200, r.text
            assert r.json()["gates"]["G0_soft"]["pass"] is True

            # Board render is queued but never blocks the story (design §3).
            r = await client.post(
                f"/api/weave/sessions/{sid}/character/board", json={},
            )
            assert r.status_code == 200, r.text
            board_jobs = r.json()["jobs"]
            assert {j["slot"] for j in board_jobs} == {"portrait", "full", "prop"}
            assert len(app.state.spooler.by_title("weave_board")) == 3

            # ── story ────────────────────────────────────────────────────────
            r = await client.post(f"/api/weave/sessions/{sid}/story/generate", json={})
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["status"] == "story"
            assert view["story_version"] == 1
            assert view["last_lint"]["pass"] is True, view["last_lint"]["defects"]
            assert view["gates"]["G1"]["pass"] is True
            assert view["gates"]["G2"]["pass"] is True
            # must_show references resolved to concrete tags (P6)
            for panel in view["panels"]:
                assert "cloth_bookmark" in panel["intent"]["must_show_resolved"]

            # Happy path spends exactly 2 LLM calls (design §2).
            assert llm.count("personalitywright") == 1
            assert llm.count("storywright") == 1
            assert llm.count("repairer") == 0
            assert llm.count("critic") == 0
            assert len(llm.calls) == 2

            # ── look-dev ─────────────────────────────────────────────────────
            r = await client.post(f"/api/weave/sessions/{sid}/lookdev")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "lookdev"

            r = await client.post(f"/api/weave/sessions/{sid}/compile")
            assert r.status_code == 200, r.text
            compiled = r.json()["compiled"]
            p1 = compiled["panel_1"]
            assert "brown_hair" in p1["positive"]
            assert "cloth_bookmark" in p1["positive"]
            # identity layer must stay free of props
            assert "cloth_bookmark" not in p1["layers"]["identity"]
            assert "cloth_bookmark" in p1["layers"]["throughline"]
            # do_not lands on the negative side
            assert "gyaru" in p1["negative"]

            # Final is refused while the board is unaccepted (G0-hard).
            r = await client.post(f"/api/weave/sessions/{sid}/render_final", json={})
            assert r.status_code == 400
            assert "framing" in r.json()["detail"] or "board" in r.json()["detail"]

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample", json={"panel_key": "panel_1"},
            )
            assert r.status_code == 200, r.text
            sample_job = r.json()["job"]
            assert app.state.spooler.by_title("weave_sample")
            queued = app.state.spooler.by_title("weave_sample")[0]["kwargs"]
            assert queued["workflow_name"] == "weave_test.json"
            assert "brown_hair" in queued["positive"]

            # G4 blocks while framing is unknown.
            r = await client.get(f"/api/weave/sessions/{sid}")
            assert r.json()["gates"]["G4"]["pass"] is False

            await _attach(
                db, sid, kind="sample", target="panel_1",
                image_id="img-sample-1", job_id=sample_job["job_id"], wd14=WIDE_TAGS,
            )
            r = await client.get(f"/api/weave/sessions/{sid}")
            view = r.json()
            assert view["panels"][0]["sample"]["image_id"] == "img-sample-1"
            assert view["panels"][0]["qa"]["framing"] == "pass"
            assert view["panels"][0]["qa"]["vlm"]["method"] == "heuristic"
            assert view["gates"]["G3"]["pass"] is True
            assert view["gates"]["G4"]["pass"] is True

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample/rate",
                json={"panel_key": "panel_1", "chips": ["good"]},
            )
            assert r.status_code == 200, r.text

            # ── board acceptance (G0-hard) ───────────────────────────────────
            for slot, iid in (("portrait", "img-b1"), ("full", "img-b2"), ("prop", "img-b3")):
                await _attach(db, sid, kind="board", target=slot, image_id=iid)
            r = await client.post(f"/api/weave/sessions/{sid}/character/accept-board", json={})
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["gates"]["G0_hard"]["pass"] is True
            assert view["next_cta"]["code"] == "render_final"

            # ── final ────────────────────────────────────────────────────────
            r = await client.post(f"/api/weave/sessions/{sid}/render_final", json={})
            assert r.status_code == 200, r.text
            final_jobs = r.json()["jobs"]
            assert len(final_jobs) == 3
            assert r.json()["status"] == "rendering"

            # Recreate is locked out while rendering (design §8).
            r = await client.post(
                f"/api/weave/sessions/{sid}/story/recreate", json={"chips": ["weak_plot"]},
            )
            assert r.status_code == 409

            # Seal is refused before finals land.
            r = await client.post(f"/api/weave/sessions/{sid}/seal")
            assert r.status_code == 400

            for job in final_jobs:
                await _attach(
                    db, sid, kind="final", target=job["panel_key"],
                    image_id=f"img-final-{job['panel_key']}", job_id=job["job_id"],
                )
            r = await client.get(f"/api/weave/sessions/{sid}/cta")
            assert r.json()["code"] == "seal"

            r = await client.post(f"/api/weave/sessions/{sid}/seal")
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["status"] == "sealed"
            assert view["seal_rubric"]["pass"] is True
            assert view["storybook_story_id"]
            assert view["next_cta"]["code"] == "done"

            # Projected into Storybook with weave provenance.
            stories = db.stories()
            assert len(stories) == 1
            story = stories[0]
            assert story["context"]["source"] == "weave"
            assert story["context"]["weave_session_id"] == sid
            assert story["axes"]["panel_1"]["image_id"] == "img-final-panel_1"

            # export bundle mirrors the sealed state
            r = await client.get(f"/api/weave/sessions/{sid}/export")
            assert r.status_code == 200
            exp = r.json()
            assert exp["status"] == "sealed"
            assert exp["panels"][0]["final_image_id"] == "img-final-panel_1"
            assert exp["panels"][0]["thumb_url"].endswith(".webp")

    run(_run())


# ── Persistence ───────────────────────────────────────────────────────────────
def test_state_survives_reload_and_lists():
    """Every handler must persist through session_db, not just mutate memory."""
    async def _run():
        db = FakeDb()
        app = build_app(db=db)
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)

            # A brand-new app instance over the same store sees the same state.
            app2 = build_app(db=db)
            async with client_for(app2) as client2:
                r = await client2.get(f"/api/weave/sessions/{sid}")
                assert r.status_code == 200
                view = r.json()
                assert view["story_version"] == 1
                assert view["character"]["identity_locked"] is True
                assert view["last_lint"]["pass"] is True

                r = await client2.get("/api/weave/sessions")
                rows = r.json()["sessions"]
                assert [row["session_id"] for row in rows] == [sid]
                assert rows[0]["topic"] == "雨の日の小さな書店"

    run(_run())


def test_unknown_session_is_404():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            for method, path in (
                ("get", "/api/weave/sessions/nope"),
                ("get", "/api/weave/sessions/nope/export"),
                ("get", "/api/weave/sessions/nope/cta"),
                ("get", "/api/weave/sessions/nope/stream"),
                ("post", "/api/weave/sessions/nope/character/lock"),
                ("post", "/api/weave/sessions/nope/lookdev"),
            ):
                r = await getattr(client, method)(path)
                assert r.status_code == 404, f"{method} {path} → {r.status_code}"

    run(_run())


# ── Guard rails ───────────────────────────────────────────────────────────────
def test_locked_identity_blocks_reinfer_until_confirmed_unlock():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)

            r = await client.post(f"/api/weave/sessions/{sid}/character/infer", json={})
            assert r.status_code == 409

            r = await client.post(
                f"/api/weave/sessions/{sid}/character/unlock", json={"confirm": False},
            )
            assert r.status_code == 400

            r = await client.post(
                f"/api/weave/sessions/{sid}/character/unlock", json={"confirm": True},
            )
            assert r.status_code == 200, r.text
            view = r.json()
            # Unlock invalidates the story (design §4.4).
            assert view["story_version"] == 0
            assert view["story_bundle"] == {}
            assert view["status"] == "character"

            r = await client.post(f"/api/weave/sessions/{sid}/character/infer", json={})
            assert r.status_code == 200, r.text

    run(_run())


def test_topic_and_author_style_are_required_for_story():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            sid = (await _create(client, topic="", author_style=""))["session_id"]
            await client.post(f"/api/weave/sessions/{sid}/character/infer", json={})
            await client.post(f"/api/weave/sessions/{sid}/character/lock")

            r = await client.post(f"/api/weave/sessions/{sid}/story/generate", json={})
            assert r.status_code == 400
            assert "topic" in r.json()["detail"]

            r = await client.patch(
                f"/api/weave/sessions/{sid}/inputs", json={"topic": "雨の書店"},
            )
            assert r.status_code == 200
            cta = r.json()["next_cta"]
            assert cta["code"] == "generate_story"
            assert cta["enabled"] is False
            assert cta["needs"] == ["author_style"]

            r = await client.post(f"/api/weave/sessions/{sid}/story/generate", json={})
            assert r.status_code == 400
            assert "author_style" in r.json()["detail"]

            r = await client.post(
                f"/api/weave/sessions/{sid}/story/generate",
                json={"author_style": "静かな日常小説"},
            )
            assert r.status_code == 200, r.text

    run(_run())


def test_story_model_is_required_no_admin_fallback():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            sid = (await _create(client, story_model=""))["session_id"]
            r = await client.post(f"/api/weave/sessions/{sid}/character/infer", json={})
            assert r.status_code == 400
            assert "story_model" in r.json()["detail"]

    run(_run())


def test_narrative_patch_allows_typos_and_rejects_rewrites():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)
            original = "店員が棚の上のしおりに気づく"

            r = await client.patch(
                f"/api/weave/sessions/{sid}/story/narrative",
                json={"panel_key": "panel_1", "narrative_ja": original + "。"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["panels"][0]["intent"]["narrative_ja"].endswith("。")

            r = await client.patch(
                f"/api/weave/sessions/{sid}/story/narrative",
                json={"panel_key": "panel_1", "narrative_ja": "まったく別の話にする。宇宙船が墜落した。"},
            )
            assert r.status_code == 400
            assert "Recreate" in r.json()["detail"]

    run(_run())


# ── Recreate / rollback ───────────────────────────────────────────────────────
def test_recreate_pushes_history_and_rollback_restores():
    async def _run():
        llm = FakeLLM()
        app = build_app(llm=llm)
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)

            llm.story = story_json(title="別の話", setting="rainy station")
            r = await client.post(
                f"/api/weave/sessions/{sid}/story/recreate",
                json={"chips": ["cliche", "more_incident"]},
            )
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["story_version"] == 2
            assert view["story_bundle"]["title"] == "別の話"
            assert len(view["story_history"]) == 1
            # chips became imperative sentences, not bare keywords
            assert view["recreate_constraints"]
            assert all(len(c.split()) > 3 for c in view["recreate_constraints"])
            # cliche feeds the avoid bank
            assert "rainy bookstore" in view["avoid_motifs"]

            r = await client.post(
                f"/api/weave/sessions/{sid}/story/rollback", json={"to_version": 1},
            )
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["story_bundle"]["title"] == "しおりの雨"
            assert view["last_lint"]["pass"] is True

            r = await client.post(
                f"/api/weave/sessions/{sid}/story/rollback", json={"to_version": 99},
            )
            assert r.status_code == 400

    run(_run())


def test_recreate_requires_chips():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)
            r = await client.post(
                f"/api/weave/sessions/{sid}/story/recreate", json={"chips": []},
            )
            assert r.status_code == 400

    run(_run())


def test_repairer_runs_once_and_rescues_a_broken_bundle():
    async def _run():
        llm = FakeLLM()
        llm.broken_story = True
        llm.repair_fixes = True
        app = build_app(llm=llm)
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await client.post(f"/api/weave/sessions/{sid}/character/infer", json={})
            await client.post(f"/api/weave/sessions/{sid}/character/lock")

            r = await client.post(f"/api/weave/sessions/{sid}/story/generate", json={})
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["last_lint"]["pass"] is True
            assert llm.count("repairer") == 1
            assert llm.count("critic") == 0
            assert view["critic_report"] is None
            assert view["next_cta"]["code"] == "enter_lookdev"

    run(_run())


def test_lint_failure_blocks_lookdev_and_offers_recreate():
    async def _run():
        llm = FakeLLM()
        llm.broken_story = True
        llm.repair_fixes = False
        app = build_app(llm=llm)
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            r = await client.post(f"/api/weave/sessions/{sid}/character/infer", json={})
            assert r.status_code == 200
            await client.post(f"/api/weave/sessions/{sid}/character/lock")

            r = await client.post(f"/api/weave/sessions/{sid}/story/generate", json={})
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["last_lint"]["pass"] is False
            assert view["gates"]["G1"]["pass"] is False
            # Repairer runs at most once, then the Critic reports.
            assert llm.count("repairer") == 1
            assert llm.count("critic") == 1
            # Defects carry a readable problem + fix (regression: duplicate keys)
            defects = view["last_lint"]["defects"]
            assert defects
            for d in defects:
                assert d["problem"], d
                assert d["fix"], d
            assert {d["code"] for d in defects} >= {"WORLD_MISSING", "VISIBLE_CHANGE_EMPTY"}
            assert view["next_cta"]["code"] == "recreate_story"

            r = await client.post(f"/api/weave/sessions/{sid}/lookdev")
            assert r.status_code == 400
            assert "recreate" in r.json()["detail"]

    run(_run())


# ── Look-dev repair loop ──────────────────────────────────────────────────────
def test_framing_fail_then_override_unblocks_g4():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)
            await client.post(f"/api/weave/sessions/{sid}/lookdev")
            await client.post(
                f"/api/weave/sessions/{sid}/sample", json={"panel_key": "panel_1"},
            )

            # Override before the fail limit is refused.
            r = await client.post(
                f"/api/weave/sessions/{sid}/sample/override-framing",
                json={"panel_key": "panel_1", "reason": "まだ試していない"},
            )
            assert r.status_code == 400

            for _ in range(2):
                r = await client.post(
                    f"/api/weave/sessions/{sid}/sample/rate",
                    json={"panel_key": "panel_1", "chips": ["too_close"]},
                )
                assert r.status_code == 200, r.text
            view = r.json()
            assert view["panels"][0]["framing_fail_count"] == 2
            assert view["gates"]["G4"]["pass"] is False
            # guided repair injected the place into the throughline
            assert "bookstore" in view["panels"][0]["intent"]["must_show_resolved"]

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample/override-framing",
                json={"panel_key": "panel_1", "reason": "引きは足りている"},
            )
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["gates"]["G4"]["pass"] is True
            assert view["framing_overrides"][0]["reason"] == "引きは足りている"

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample/override-framing",
                json={"panel_key": "panel_1", "reason": ""},
            )
            assert r.status_code == 400

    run(_run())


def test_sparse_chip_thickens_environment_layer():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)
            r = await client.post(f"/api/weave/sessions/{sid}/lookdev")
            before = r.json()["panels"][1]["compile"]["layers"]["environment"]

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample/rate",
                json={"panel_key": "panel_2", "chips": ["sparse"]},
            )
            assert r.status_code == 200, r.text
            after = r.json()["panels"][1]["compile"]["layers"]["environment"]
            assert len(after) > len(before)
            assert "detailed_background" in after

    run(_run())


def test_unclear_story_chip_routes_to_recreate_not_edits():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            view = await _lock_and_story(client, sid)
            before = view["story_bundle"]["panels"][0]["narrative_ja"]
            await client.post(f"/api/weave/sessions/{sid}/lookdev")

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample/rate",
                json={"panel_key": "panel_1", "chips": ["unclear_story"]},
            )
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["suggest_recreate"] is True
            # The story text itself is never touched by look-dev.
            assert view["story_bundle"]["panels"][0]["narrative_ja"] == before

    run(_run())


def test_placeholder_sample_requires_lab_mode():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample",
                json={"panel_key": "panel_1", "placeholder": True},
            )
            assert r.status_code == 400
            assert "lab" in r.json()["detail"]

            r = await client.patch(
                f"/api/weave/sessions/{sid}/inputs", json={"mode": "lab"},
            )
            assert r.status_code == 200
            r = await client.post(
                f"/api/weave/sessions/{sid}/sample",
                json={"panel_key": "panel_1", "placeholder": True},
            )
            assert r.status_code == 200, r.text
            assert r.json()["panels"][0]["sample"]["image_id"] == "placeholder:panel_1"
            # Placeholders never satisfy G0-hard.
            r = await client.post(
                f"/api/weave/sessions/{sid}/character/accept-board", json={},
            )
            assert r.status_code == 400

    run(_run())


def test_multi_seed_queues_alternates_and_adopt_promotes():
    async def _run():
        db = FakeDb()
        app = build_app(db=db)
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)
            await client.patch(f"/api/weave/sessions/{sid}/inputs", json={"multi_seed": 3})

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample", json={"panel_key": "panel_1"},
            )
            assert r.status_code == 200, r.text
            jobs = r.json()["jobs"]
            assert len(jobs) == 3

            for i, job in enumerate(jobs):
                await _attach(
                    db, sid, kind="sample", target="panel_1",
                    image_id=f"img-seed-{i}", job_id=job["job_id"], seed_index=i,
                    wd14=WIDE_TAGS,
                )
            r = await client.get(f"/api/weave/sessions/{sid}")
            panel = r.json()["panels"][0]
            assert panel["sample"]["image_id"] == "img-seed-0"
            assert len(panel["sample_history"]) == 3

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample/adopt",
                json={"panel_key": "panel_1", "image_id": "img-seed-2"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["panels"][0]["sample"]["image_id"] == "img-seed-2"

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample/adopt",
                json={"panel_key": "panel_1", "image_id": "nope"},
            )
            assert r.status_code == 400

    run(_run())


def test_reeval_framing_upgrades_unknown_to_pass():
    async def _run():
        db = FakeDb()
        app = build_app(db=db)
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)
            r = await client.post(
                f"/api/weave/sessions/{sid}/sample", json={"panel_key": "panel_1"},
            )
            job = r.json()["job"]
            # Image arrives with no WD14 → framing unknown, G4 stays shut.
            await _attach(
                db, sid, kind="sample", target="panel_1",
                image_id="img-untagged", job_id=job["job_id"], wd14=[],
            )
            r = await client.get(f"/api/weave/sessions/{sid}")
            view = r.json()
            assert view["panels"][0]["qa"]["framing"] == "unknown"
            assert view["gates"]["G4"]["pass"] is False
            assert view["gates"]["G4"]["pending"] is True
            assert view["next_cta"]["code"] == "reeval_framing"

            db.images["img-untagged"]["wd14_tags"] = WIDE_TAGS
            r = await client.post(f"/api/weave/sessions/{sid}/sample/reeval-framing")
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["panels"][0]["qa"]["framing"] == "pass"
            assert view["gates"]["G4"]["pass"] is True

    run(_run())


def test_score_and_vlm_assist_endpoints():
    async def _run():
        db = FakeDb()
        app = build_app(db=db)
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            await _lock_and_story(client, sid)
            r = await client.post(
                f"/api/weave/sessions/{sid}/sample", json={"panel_key": "panel_1"},
            )
            job = r.json()["job"]
            await _attach(
                db, sid, kind="sample", target="panel_1",
                image_id="img-s", job_id=job["job_id"], wd14=WIDE_TAGS,
            )

            r = await client.post(f"/api/weave/sessions/{sid}/score")
            assert r.status_code == 200, r.text
            assert 0.0 <= r.json()["weave_score"]["overall"] <= 1.0

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample/vlm-assist",
                json={"panel_key": "panel_1", "force_heuristic": True},
            )
            assert r.status_code == 200, r.text
            assert r.json()["vlm_assist"]["method"] == "heuristic"

            r = await client.post(
                f"/api/weave/sessions/{sid}/sample/vlm-assist",
                json={"panel_key": "panel_2", "force_heuristic": True},
            )
            assert r.status_code == 400  # no sample on panel_2

    run(_run())


def test_llm_provider_binding_is_honored():
    async def _run():
        llm = FakeLLM()
        app = build_app(llm=llm)
        async with client_for(app) as client:
            sid = (await _create(client, llm_provider="openai"))["session_id"]
            await client.post(f"/api/weave/sessions/{sid}/character/infer", json={})
            assert llm.bound == ["openai"]

    run(_run())


def test_catalog_endpoint():
    async def _run():
        app = build_app()
        async with client_for(app) as client:
            r = await client.get("/api/weave/catalog")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert body["comfyui"]["workflows"] == ["weave_test.json"]

    run(_run())


def test_writes_publish_session_events():
    """SSE subscribers must see saves and render attaches.

    The stream endpoint itself is not exercised here: httpx's ASGITransport
    buffers the whole response body, so an endless SSE generator never yields
    headers. The fan-out below is what the stream forwards.
    """
    async def _run():
        from app.weave.events import subscribe, unsubscribe

        db = FakeDb()
        app = build_app(db=db)
        async with client_for(app) as client:
            sid = (await _create(client))["session_id"]
            queue = await subscribe(sid)
            try:
                r = await client.patch(
                    f"/api/weave/sessions/{sid}/inputs", json={"topic": "別のお題"},
                )
                assert r.status_code == 200, r.text
                evt = queue.get_nowait()
                assert evt["type"] == "session_updated"
                assert evt["session_id"] == sid

                await _attach(
                    db, sid, kind="board", target="portrait", image_id="img-p",
                )
                events = []
                while not queue.empty():
                    events.append(queue.get_nowait())
                assert any(e["type"] == "render_attached" for e in events)
            finally:
                await unsubscribe(sid, queue)

    run(_run())
