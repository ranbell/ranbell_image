"""制作スタッフ: PLAN/COSTUME → living notebook → scripter craft compile."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook, shared


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)




def test_trait_blurb_reflects_busy_vs_simple_background():
    from app.muse import crew
    busy = crew.trait_blurb("propshop:takarabako", locale="ja")
    simple = crew.trait_blurb("propshop:yohaku", locale="ja")
    assert "情報量" in busy or "物量" in busy
    assert "余白" in simple or "空ける" in simple


def test_preset_meta_exposed_on_roster():
    from app.muse import crew
    roster = crew.public_roster()
    assert "calm" in roster["preset_meta"]
    assert roster["preset_meta"]["calm"]["look_ja"]
    assert roster["preset_meta"]["calm"]["team_ja"] == "チームパステル"
    assert roster["preset_meta"]["vivid"]["team_ja"] == "チーム彩宴"
    assert roster["preset_meta"]["photoreal"]["team_ja"] == "チームフィルム"


def test_person_cards_expose_vibe_and_shoot_style():
    from app.muse import crew
    roster = crew.public_roster()
    soft = next(m for m in roster["muses"] if m["id"] == "gaffer:andon")
    assert soft["vibe_ja"]
    assert "パステル" in soft["shoot_style_ja"] or "包" in soft["shoot_style_ja"]
    gate = next(m for m in roster["muses"] if m["id"] == "gate:mon")
    assert "やさしい" in gate["vibe_ja"] or "優しい" in gate["voice_ja"]
    assert gate["say_examples"]
    prompt = crew.system_prompt_for("gate:mon")
    assert "ROOM VIBE" in prompt or "やさしい" in prompt
    assert "厳しい編集者" not in crew.MUSES["ink:ipponsen"]["voice_ja"]
    assert "即却下" not in crew.MUSES["ink:ipponsen"]["voice_ja"]


def test_packed_prompt_carries_each_person_card():
    """1行ロスターではなく、席ごとの声・口調・例セリフが入る。"""
    from app.muse import crew
    speakers = ["wardrobe:shiwa", "spine:bane", "gaffer:gyakkou"]
    prompt = crew.table_talk_system_prompt(
        speakers, base_style="anime", locale="ja",
        preset_id="standard", seed="sess-1", lead_name="花",
    )
    for mid in speakers:
        assert f"`{mid}`" in prompt
        assert crew.MUSES[mid]["voice_ja"] in prompt
        assert crew.MUSES[mid]["line_ja"] in prompt
        assert crew._pick_say_example(mid, "sess-1") in prompt
    # 反応の契約（名指し・エコー禁止・主演に向けて話す）
    assert "names the person before them" in prompt
    assert "echo is not a reaction" in prompt
    assert "花" in prompt


# ── CREW LOOK: 専門席の仕事が weave まで届く ────────────────────────────────
def test_craft_slots_have_one_owner_each():
    from app.muse import crew
    assert crew.craft_slot("gaffer:gyakkou") == "LIGHT"
    assert crew.craft_slot("lens:pinto") == "OPTICS"
    # 服そのものはノートの WEARING（所有者は台本）。衣装席は生地だけ。
    assert crew.craft_slot("wardrobe:shiwa") == "CLOTH"
    # ポーズはノートの BEAT が正本。演出/振付は BODY スロットで weave まで届ける
    # （talk group で口は一人なので同じ鍵を共有してよい）。
    assert crew.craft_slot("beat:ichibyou") == "BODY"
    assert crew.craft_slot("spine:bane") == "BODY"
    owned = list(crew.CRAFT_SLOTS.values())
    # BODY だけ beat+spine で共有。他は一人一枠。
    assert owned.count("BODY") == 2
    assert len(set(owned)) == len(owned) - 1


def test_light_is_its_own_field_end_to_end():
    """「逆光にして」が scene や atmosphere に紛れず、次のターンで消えない。"""
    session = {"mode": "duet", "inputs": {"locale": "ja"}, "notebook": notebook.blank()}
    nb = notebook.of(session)
    notebook.apply_patch(nb, {"scene": "a classroom at dusk", "light": "backlit, hard rim"})
    assert nb["light"] == "backlit, hard rim"
    # 別のフィールドを書き換えても光は残る
    notebook.apply_patch(nb, {"beat": "standing"})
    assert nb["light"] == "backlit, hard rim"
    # ノートの表示にも出るので、台本も主演も読める
    assert "LIGHT:" in notebook.render(nb)
    # 台本の出力（ラベル / JSON どちらでも）から取り込める
    assert notebook.parse_scripter(
        "INTENT: shot\nLIGHT: one lantern at floor level"
    )["patch"]["light"] == "one lantern at floor level"


# ── struck は「いま写っているもの」を締め出してはいけない ──────────────────
def test_struck_never_holds_what_the_shot_now_says():
    """立ち上がった後にまた座れる。struck は追記専用の墓場ではない。"""
    session = {"mode": "", "inputs": {"locale": "ja"}, "notebook": notebook.blank()}
    nb = notebook.of(session)
    notebook.apply_patch(nb, {"beat": "sitting on the bench", "wearing": "sailor uniform, straw hat"})
    # 立ち上がる → sitting が struck に入る
    notebook.record_struck_tokens(session, prev="sitting on the bench", new="standing", min_len=4)
    notebook.apply_patch(nb, {"beat": "standing, holding the hem"})
    assert "sitting" in notebook.struck_tokens(session)
    # 帽子を取る → straw_hat も struck
    notebook.record_struck_from_wearing(
        session, prev_wearing="sailor uniform, straw hat", new_wearing="sailor uniform",
    )
    notebook.apply_patch(nb, {"wearing": "sailor uniform"})
    assert "straw_hat" in notebook.struck_tokens(session)
    # また座らせたら、sitting は締め出しから外れる（帽子は外れたまま）
    notebook.apply_patch(nb, {"beat": "sitting on the floor"})
    live = notebook.struck_tokens(session)
    assert "sitting" not in live
    assert "straw_hat" in live
    assert "sitting" not in " ".join(notebook.live_struck(session))


def test_struck_does_not_mint_grammar_pairs():
    """文をまたいだ語のペアは物の名前ではない。"""
    toks = notebook.wearing_tokens(
        "sitting on the wooden bench while staring at nothing",
    )
    assert "wooden_bench" in toks
    for junk in ("on_the", "the_wooden", "while_staring", "at_nothing", "bench_while"):
        assert junk not in toks


def test_removed_garment_is_not_put_back_by_coverage():
    """脱がせた服は復活させない（drop_banned の抜け道を作らない）。"""
    session = {
        "mode": "", "inputs": {"locale": "ja"}, "notebook": notebook.blank(),
        "craft": {}, "character": {}, "banned": ["straw_hat"],
    }
    notebook.apply_patch(notebook.of(session), {"wearing": "sailor uniform"})
    notebook.record_struck_from_wearing(
        session, prev_wearing="sailor uniform, straw hat",
        new_wearing="sailor uniform",
    )
    tags, _ = notebook.reconcile_wardrobe_tags(
        "1girl, sailor_uniform",
        wearing="sailor uniform",
        struck=notebook.struck_tokens(session),
        banned={"straw_hat"},
    )
    assert "straw_hat" not in tags


# ── ルックの明示指定・strike の誤爆・提案の経路 ─────────────────────────────
def test_named_look_beats_the_room_average():
    """16席の平均は常に無難な真ん中に落ちる。名前で呼べば総監督が決める。"""
    from app.muse import crew
    cast = crew.resolve_crew(preset="standard")
    assert crew.base_style_for(cast, "", "") == "anime illustration"  # 平均の実測値
    assert crew.base_style_for(cast, "", "vivid") == "vivid anime illustration"
    assert crew.base_style_for(cast, "", "flat") == "flat anime cel shading"
    # 総監督が文で書いたものより、名指しのルックが強い。
    assert crew.base_style_for(cast, "水彩っぽく", "flat") == "flat anime cel shading"
    # 知らない名前は無視して従来どおり。
    assert crew.base_style_for(cast, "水彩っぽく", "nonsense") == "水彩っぽく"


def test_fold_moves_the_body_and_nothing_else():
    """fold が触れるのは beat だけ。絵そのものは総監督の指示でしか動かない。

    提案欄 `open` は撤去した。390セッションで一度も提案が入らず、入っていた
    50件は `$$OPEN$$` や `clear_open: true` といったパーサのゴミで、それが
    台本のプロンプトに戻りパネルにも出ていた。席の提案は chat に残る。
    """
    assert notebook.FOLD_PATCH_KEYS == ("beat", "beat_b")
    from app.muse import chain
    assert "crew's lines from this turn" in chain.SCRIPTER_FOLD_NOTE
    assert "open" not in chain.SCRIPTER_FOLD_NOTE


def test_weave_is_told_the_camera_is_not_a_subject():
    from app.muse import chain
    assert "THE CAMERA IS NOT IN THE PICTURE" in chain.SCRIPTER_WEAVE_SYSTEM
    assert "Never write her name" in chain.SCRIPTER_WEAVE_SYSTEM


def test_the_partner_wardrobe_is_restored_too():
    """相方の服も戻す（旧: WEARING_B だけ weave に落とされたまま）。"""
    session = {
        "mode": "", "session_id": "s-w", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(partner=True), "craft": {},
        "character": {}, "partner_character": {"name": "Sumire Hiraoka"},
    }
    notebook.apply_patch(notebook.of(session), {
        "wearing": "professional blouse", "wearing_b": "linen apron",
    })
    tags, _ = notebook.reconcile_wardrobe_tags(
        "2girls, professional_blouse",
        wearing="professional blouse", wearing_b="linen apron",
        partner=True,
    )
    assert "linen_apron" in tags


def _w_session(**over) -> dict:
    session = {
        "mode": "", "session_id": "s-sides", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(partner=True), "craft": {},
        "character": {"name": "Mio Kagami"},
        "partner_character": {"name": "Sumire Hiraoka"},
    }
    session.update(over)
    return session


def test_the_photo_is_not_read_back_into_the_notebook():
    """写真読みの配線を外した。**手帖は会話で書かれるのが正本。**"""
    import inspect

    from app.muse import runner as muse_runner
    src = inspect.getsource(muse_runner)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "still_read_after_board" not in code


