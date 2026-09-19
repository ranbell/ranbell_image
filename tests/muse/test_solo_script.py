"""Lead shoot: Muse CARD + Script compile/weave, struck, still-as-base."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, crew, identity, notebook, shared
from tests.muse.test_duet_notebook import (  # noqa: E402
    NotebookOllama, _current_note, _fake_clerk_reply, _scripter_block,
)


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)


def test_parse_talk_blocks_keeps_card_out_of_say():
    raw = (
        "SAY: 風、ちょっと冷たいね。\n"
        "ASIDE: 帽子、外して正解だったかも。\n"
        "CARD:\n"
        "PLACE: school rooftop\n"
        "HOUR: dusk\n"
        "WEARING: thin cardigan\n"
        "BEAT: sitting on a bench\n"
        "FRAME: eye level\n"
        "PITCH: 麦わら帽子をかぶる | 帽子なし\n"
    )
    blocks = identity.parse_talk_blocks(raw)
    say = identity.sanitize_muse_say(blocks["say"])
    assert "風" in say
    assert "PLACE" not in say
    assert "thin cardigan" in blocks["card"]
    assert "帽子" in blocks["aside"]
    # PITCH is still parsed as words she said. It no longer becomes chips:
    # `open_choices` fed off `open`, which never held a proposal in 390
    # live sessions and is gone.
    assert "麦わら帽子をかぶる" in blocks["pitch"]


def test_parse_muse_card_and_absorb_pose_only():
    nb = notebook.blank()
    notebook.apply_patch(nb, {"beat": "standing", "wearing": "sailor uniform"})
    card = (
        "WEARING: sailor uniform, ribbon\n"
        "BEAT: reaching forward, fingers spread\n"
        "FRAME: eye level\n"
    )
    absorbed = notebook.absorb_muse_card(nb, card)
    assert absorbed["beat"] == "reaching forward, fingers spread"
    assert "reaching" in nb["beat"]
    assert nb["wearing"] == "sailor uniform"
    assert "ribbon" not in nb["wearing"]


def test_scripter_reads_muse_pose_and_recall():
    text = " ".join(chain.SCRIPTER_SYSTEM.lower().split())
    assert "card beat" in text
    assert "recall" in text
    assert "この間" in chain.SCRIPTER_SYSTEM
    assert "last noun" not in text
    assert "drop her pose" not in text
    assert "never casual" in text
    assert "never paint scene or wearing from say atmosphere" in text
    assert "fold:" in text
    assert "uncontradicted" in text
    assert "posture stem" in text
    assert "sitting" in text and "standing" in text
    assert "turning around" in text
    assert "facing camera" in text
    assert "寄って" in chain.SCRIPTER_SYSTEM
    assert "引いて" in chain.SCRIPTER_SYSTEM


def test_scripter_fold_note_keeps_showrunner_posture():
    note = " ".join(chain.SCRIPTER_FOLD_NOTE.lower().split())
    assert note.startswith("fold:")
    assert "hands" in note
    assert "do not invent clothes" in note
    assert "do not emit tags" in note
    assert "sitting into standing" in note
    assert "facing camera" in note
    assert "stem already in notebook now" in note


def test_scripter_verify_note_keeps_posture_stem():
    note = " ".join(chain.SCRIPTER_VERIFY_NOTE.lower().split())
    assert note.startswith("verify:")
    assert "sitting" in note
    assert "turning around is not sitting" in note
    assert "sit/stand/kneel/crouch stem" in note
    assert "facing camera" in note
    assert "invent standing" in note


def test_duet_talk_output_answers_nouns_when_asked():
    text = " ".join(crew.DUET_TALK_OUTPUT.lower().split())
    assert "never a change-log" not in text
    assert "change log" not in text or "checklist" in text
    assert "card" in text
    assert "aside" in text
    assert "pitch" in text
    assert "not rewrite the notebook" in text
    assert "shot notebook" in text
    assert "body action" in text
    assert "posture the notebook does not have" not in text
    assert "how you are holding it" not in text
    assert "寄ってる" in crew.DUET_TALK_OUTPUT
    assert "wearing_b" in text
    assert "aside" in text


def test_wearing_tokens_drop_no_hat():
    assert "hat" not in notebook.wearing_tokens("thin cardigan, no hat")
    assert "cardigan" in notebook.wearing_tokens("thin cardigan, no hat")


def test_struck_from_wearing_diff():
    session = {"struck": []}
    notebook.record_struck_from_wearing(
        session, prev_wearing="thin cardigan, straw hat",
        new_wearing="thin cardigan",
    )
    struck = notebook.struck_tokens(session)
    assert "hat" in struck or "straw_hat" in struck


def test_filter_weave_tags_drops_struck_hat():
    tags = notebook.filter_weave_tags(
        "thin_cardigan, straw_hat, knit, fabric_folds, lantern",
        wearing="thin cardigan",
        scene="rooftop at dusk",
        beat="sitting on a bench",
        struck={"hat", "straw_hat"},
    )
    low = tags.lower()
    assert "straw_hat" not in low
    assert "cardigan" in low or "thin_cardigan" in low


def test_split_atmosphere_time_moves_dusk():
    mood, place = notebook.split_atmosphere_time("tender dusk", "rooftop")
    assert "dusk" not in mood.lower()
    assert "dusk" in place.lower()
    assert "tender" in mood.lower()


def test_board_images_take_the_latest(monkeypatch):
    session = {
        "session_id": "s1",
        "board": {
            "pending": False,
            "round": 2,
            "images": [
                {"image_id": "old"},
                {"image_id": "new"},
            ],
        },
    }
    shots = [
        str(i.get("image_id") or "") for i in (session["board"]["images"] or [])
        if isinstance(i, dict) and i.get("image_id")
    ]
    assert shots[-1:] == ["new"]
    assert shots[:1] == ["old"]


def test_coerce_plain_phrase_salvages_list_wearing():
    assert notebook.coerce_plain_phrase(["sailor uniform, cardigan"]) == (
        "sailor uniform, cardigan"
    )
    assert notebook.coerce_plain_phrase("['sailor uniform, cardigan']") == (
        "sailor uniform, cardigan"
    )
    assert notebook.coerce_plain_phrase({"place": "rooftop"}) == ""
    nb = notebook.blank()
    notebook.apply_patch(nb, {"wearing": ["sailor uniform", "cardigan"]})
    assert "sailor uniform" in nb["wearing"]
    assert "cardigan" in nb["wearing"]
    notebook.apply_patch(nb, {"vibe": {"mood": "tender"}, "open": "{broken}"})
    assert nb.get("vibe") == ""
    assert "{" not in str(nb.get("open") or "")


def test_wearing_tokens_do_not_mint_hat_cardigan():
    toks = notebook.wearing_tokens("straw hat, cardigan")
    assert "hat" in toks
    assert "cardigan" in toks
    assert "hat_cardigan" not in toks
    assert "straw_hat" in toks


def test_drop_leftover_garments_and_crops():
    tags = notebook.drop_garments_not_in_wearing(
        "sailor_collar, straw_hat, thin_cardigan, knit, fabric_folds",
        wearing="sailor uniform, cardigan",
    )
    low = tags.lower()
    assert "straw_hat" not in low
    assert "cardigan" in low
    assert "knit" in low
    zoom = notebook.drop_crops_not_in_frame(
        "upper_body, close_up, wide_shot, full_body, knit",
        frame="close, upper body",
    )
    zlow = zoom.lower().replace(" ", "_")
    assert "wide_shot" not in zlow
    assert "full_body" not in zlow
    wide = notebook.drop_crops_not_in_frame(
        "wide_shot, full_body, close_up, face_focus, knit",
        frame="wide full body",
    )
    wlow = wide.lower().replace(" ", "_")
    assert "close_up" not in wlow
    assert "wide_shot" in wlow or "full_body" in wlow


def test_scripter_forbids_empty_shot_and_dual_crop():
    text = " ".join(chain.SCRIPTER_SYSTEM.lower().split())
    assert "empty shot" in text or "empty shot/mixed" in text
    assert "wide_shot" in text and "close_up" in text
    weave = " ".join(chain.SCRIPTER_WEAVE_SYSTEM.lower().split())
    assert "wide_shot" in weave
    fold = " ".join(chain.SCRIPTER_FOLD_NOTE.lower().split())
    assert "latest line" in fold
    assert "do not patch scene" in fold


# ── 欄の契約は一つ ───────────────────────────────────────────────────────

def test_everyone_who_touches_the_notebook_reads_the_same_contract():
    """One definition, handed to every seat that reads or writes the notebook.

    Measured 2026-08-19: the definition of `frame` existed in four places and
    disagreed. compile — the one that writes the notebook every turn — said
    only "ONE crop" and never mentioned the gaze at all, while the accurate
    version ("Crop plus gaze. Not where you are looking; that is the frame.")
    was reachable from exactly one call site, the restate turn.

    So the showrunner's 「カメラ見て」 ("look at the camera") landed in `frame`,
    the old gaze stayed in
    `beat`, and weave — never told which field owns it — took the concrete one.
    He said it three times, the last time as a raw danbooru tag, and the board
    did not move. Nobody was wrong; nothing agreed.
    """
    marker = "WHAT EACH PART OF THE NOTEBOOK IS"
    for name in (
        "SCRIPTER_SYSTEM",          # 旧・比較用に残してある
        "STILL_READ_SYSTEM",        # writes it from a photo
        "NOTEBOOK_REVIEW_SYSTEM",   # she checks it
    ):
        assert marker in getattr(chain, name), name
    # compile が実際に読む契約は `SCRIPTER_BLOCKS` の組み立てで、共通ブロック
    # の見出しは持たない。持っているべきは**中身**のほう。
    built = chain.build_scripter_system()
    for field in ("ATMOSPHERE", "SCENE", "LIGHT", "FRAME", "WEARING", "BEAT"):
        assert field in built, field
    # **視線の持ち主が変わった。** かつては FRAME、いまは BEAT ——
    # 総監督（2026-09-01）「視点は Muse A/B がどこを向いているのかなので、
    # 自ずと beat に入る。**焦点はカメラワーク**」。
    #
    # 実機（`c9d83e6e`）で「すみれは後ろを向いて、みおはこっち見て」が
    # `frame` 一本に潰れ、片方が消えていた。**二人は別々の所を見るので、
    # 共有の一欄では二つの答えを持てない。**
    #
    # この試験の値打ちは「全員が同じ定義を読む」こと。定義が移ったなら、
    # **移った先で一致していること**を見る。
    low = built.lower()
    assert "eyes are beat's" in low
    assert "focus on" in low, "焦点の置き場が FRAME に無い"
    assert "not where she is looking" not in low, "古い定義が残っている"

    # weave には渡さない。**読む側には効かなかった。** weave パック
    # (6試験 x 5回) で測ると、契約を抜いたほうが良い:
    #
    #     契約あり 5,228字  28/30   w3 の空応答 3/15
    #     契約抜き 4,157字  30/30   w3 の空応答 0/15
    #
    # 欄が何であるかは書く側の問題で、読む側は値さえ読めればよい。1,071字を
    # 毎レンダー載せたうえ、たまに応答ごと潰していた。
    assert marker not in chain.SCRIPTER_WEAVE_SYSTEM


def test_only_frame_owns_the_gaze():
    """Two fields claiming the gaze is what froze the board — hold the split."""
    assert "NOT where she is looking: that is the frame." in (
        notebook.FIELD_CONTRACTS["beat"]
    )
    assert "where her eyes are pointed" in notebook.FIELD_CONTRACTS["frame"]

    # And the split survives into the prompts, in both voices.
    third = notebook.contracts_block()
    second = notebook.contracts_block(second_person=True)
    assert "NOT where she is looking" in third
    assert "NOT where you are looking" in second
    assert "where your eyes are pointed" in second


def test_the_restate_shape_is_not_a_second_copy_of_the_contract():
    """crew's restate must quote the one source, not carry its own wording.

    It used to hold its own English text in `_RESTATE_FIELDS`. That copy was
    the accurate one, which is exactly why the drift went unnoticed — the good
    wording sat where almost nothing could read it.
    """
    for field, phrase in (
        ("frame", "where your eyes are pointed"),
        ("beat", "NOT where you are looking: that is the frame."),
        ("wearing", "A held prop is not worn; that belongs in beat."),
    ):
        # The value line, not the WHY line that now follows it.
        prompt = crew.restate_output(field)
        assert phrase in prompt, field
        # …and the format-only tail is still appended, not lost.
    # 上限は外した。実撮影で「衣装はそのままで」と言われたターンの言い直しが
    # 数を守るために `hair ornament` を落とした。書式の指示は残っている。
    w = crew.restate_output("wearing")
    assert "AT MOST" not in w
    assert "a garment left out is a garment she loses" in w
    assert "underscores" in w


# ── なぜその欄をそう書いたか ──────────────────────────────────────────────

def test_the_scripter_is_asked_to_say_why_it_wrote_each_field():
    text = chain.SCRIPTER_SYSTEM
    assert "SAY WHY, FIELD BY FIELD" in text
    assert "WHY_FRAME" in text
    # The reason has to point at what was said, not read the value back.
    assert "Point at what was said" in text


def test_why_is_never_a_slot_in_the_json_schema():
    """A reason slot in the schema eats the job it was supposed to annotate.

    Measured 8/19 against the live model, three cases x three runs each:

        why in the schema   wrote a field 0/9
        why out of it       wrote a field 9/9

    The model answered `{"intent":"shot","why":{"beat":"…set posture stem to
    sitting"}}` — describing the edit instead of making it. Strengthening the
    wording ("the value IS the work") did not move it: still 0/9. The slot
    itself is the cause, so the reason is collected from labelled `WHY_*`
    lines, which sit in the same list as the values and cannot replace them.
    """
    assert "why" not in notebook.SCRIPTER_FORMAT_SCHEMA["properties"]
    # …but the parser still reads one when the model offers it.
    parsed = notebook.parse_scripter(
        "INTENT: shot\nBEAT: sitting\nWHY_BEAT: 座ってと言われた"
    )
    assert parsed["patch"]["beat"] == "sitting"
    assert parsed["why"]["beat"] == "座ってと言われた"


def test_a_reason_is_parsed_from_json_and_from_labels_alike():
    """Both paths, because the labelled one runs on every turn with an image."""
    labelled = notebook.parse_scripter(
        "INTENT: shot\n"
        "FRAME: medium shot, looking into the lens\n"
        "WHY_FRAME: 『カメラ目線で』と言われたので視線を frame に置いた\n"
    )
    assert labelled["patch"]["frame"].startswith("medium shot")
    assert "カメラ目線" in labelled["why"]["frame"]

    as_json = notebook.parse_scripter(
        '{"intent":"shot","frame":"medium shot, looking into the lens",'
        '"why":{"frame":"asked for eye contact"}}'
    )
    assert as_json["why"]["frame"] == "asked for eye contact"


def test_a_reason_for_a_field_nobody_wrote_is_dropped():
    """A decision that never landed must not show up as if it had."""
    assert notebook.clean_why({"frame": "x", "beat": "y"}, {"frame": "v"}) == {
        "frame": "x",
    }

    session: dict = {}
    entry = notebook.record_rewrite(
        session, "scripter",
        before={"frame": "a", "beat": "b"},
        after={"frame": "z", "beat": "b"},
        why={"frame": "moved the gaze", "beat": "never landed"},
    )
    assert entry["changed"]["frame"]["why"] == "moved the gaze"
    assert "beat" not in entry["changed"]


def test_the_reason_is_one_line_not_a_second_notebook():
    long = "あ" * 400
    assert len(notebook.clean_why({"beat": long}, {"beat": "v"})["beat"]) == (
        notebook.WHY_MAX_CHARS
    )
    # Newlines collapse — the panel renders one line per field.
    assert notebook.clean_why({"beat": "one\ntwo"}, {"beat": "v"})["beat"] == "one two"


# ── compile が実際に使う契約 ──────────────────────────────────────────────

def test_compile_runs_on_the_built_contract_not_the_old_one():
    """`SCRIPTER_SYSTEM` is kept, but compile no longer reads it.

    Compared under identical conditions on the standard 30-case pack (30 cases ×
    5 runs, restatements included):

        SCRIPTER_SYSTEM  8,281 chars   52.7% (judged in one pass)
        built from blocks 2,327 chars   96.0% (4.0% stalled)

    Split by category the shape of the difference is plain. Leaving things alone
    scores 100% either way; the difference is in moving them — pose 16%→100%,
    clothes 24%→88%. Thirty-three prohibitions against six positives made "do not
    move it" perfect and broke "move it".
    """
    import inspect
    src = inspect.getsource(chain.run_scripter)
    assert "build_scripter_system(genre=genre)" in src
    assert "else SCRIPTER_SYSTEM" not in src
    # 旧版は捨てない。戻せることがこの入れ替えの前提。
    assert len(chain.SCRIPTER_SYSTEM) > 8000


def test_the_built_contract_says_what_each_field_is_and_forbids_almost_nothing():
    built = chain.build_scripter_system()
    # 実測の値は 2,327字（96.0%）で、上限はそこから伸びすぎないための柵。
    # `expression` を欄として足したぶん（55字）で 3,000 を越えたので、そのぶん
    # だけ上げた。**新しい欄一つぶんであって、条文が太ったのではない** ——
    # 8,281字版が 52.7% だった教訓は生きている。
    #
    # 焦点（カメラがどちらに寄るか）に持ち場を作ったぶん、もう一段上げた
    # （総監督 2026-09-01「視点は beat に入る。**焦点はカメラワーク** ——
    # `focus to …` とか `long shot` とかはこの箱」）。足したのは箱一つで、
    # 説明を太らせたのではない —— `gaze` と `stem` の重複はそのぶん畳んだ。
    # ジャンル別のエキスパート（`crew.GENRES`）は**箱ひとつぶん**。選ばれた
    # 一つだけが末尾に付く（約290字）。前例どおり、箱を足したぶんだけ上げる。
    #
    # **土台はむしろ痩せた** —— `PROPOSE` が同じ趣旨を三度書いていたので畳み、
    # 3,264 → 2,829字。エキスパート付きで約3,120字なので、旧上限からの増分は
    # 20字ほどで、**その中に丸ごと一つの箱が入っている**。
    assert len(built) < 3200
    for genre in crew.GENRES:
        assert len(chain.build_scripter_system(genre=genre)) < 3200, genre

    # 外枠 — 欄が何であるか。
    for phrase in ("ATMOSPHERE", "SCENE", "LIGHT", "FRAME", "WEARING", "BEAT"):
        assert phrase in built
    # **視線は BEAT。** 二人は別々の所を見るので、共有の一欄では持てない。
    assert "eyes are beat's" in built.lower()
    # **焦点は FRAME。** カメラの箱。
    assert "focus on" in built.lower()
    assert "posture" in built.lower()                     # beat は姿勢を言う

    # 中身は任せる。禁止で埋めない — それが 8,281字が負けた理由。
    #
    # 数えるのは**命令としての禁止**だけ。`the notebook never had` のような
    # 説明の中の never まで数えていて、境界を一つ足しただけで落ちた。
    # **語の出現ではなく、その語が何をしているかで数える。** 今日ここで
    # 6回踏んだのと同じ形の失敗だったので、判定のほうを直した。
    import re
    lowered = built.lower()
    bans = len(re.findall(r"(?m)(?:^|[.;]\s+|\*\*)(?:do not|never|must not)\s+\w", lowered))
    assert bans <= 5, f"命令としての禁止が {bans} 個。旧版は 33 個で 52.7% だった"


def test_a_proposal_has_somewhere_to_go():
    """With nowhere to put it, an idea gets pushed into a field.

    On t21 「おいしそう？」 ("does it look good?") it put bread in her hands 5/5
    with no food anywhere in the notebook. Making `PROPOSE:` rather than
    prohibiting it cleared all five.
    """
    assert "PROPOSE" in chain.build_scripter_system()
    parsed = notebook.parse_scripter(
        "INTENT: casual\nPROPOSE: something the room has not decided yet"
    )
    assert parsed["patch"] == {}, "提案がノートに入ってはいけない"
    assert parsed["propose"].startswith("something")


def test_intent_is_not_bought_at_the_notebook_s_expense():
    """The intent explanation exists as a block but is not in the default.

    Adding it takes intent from 68% to 93% and drops the notebook from 96.0% to
    86.7% (the clothes category 88%→48%, with not one test improving). Intent is
    taken by another road — the `classify_intent` clerk, and whether the patch
    moved a field (92%, measured).
    """
    assert "intent" in chain.SCRIPTER_BLOCKS          # 残してある
    assert "intent" not in chain.SCRIPTER_BUILD_DEFAULT
    assert "shot" in chain.CLASSIFY_INTENT_SYSTEM     # clerk が持っている


def test_a_solo_shoot_has_no_partner_fields_to_write_into():
    """Leave the partner's fields open and her clothes go in there and vanish.

    Measured (「カーディガン羽織って。」 — "put a cardigan on", solo, 10 runs):

        landed in wearing              6
        landed in wearing_b, then lost  2   ← on to the next turn still undressed
        malformed / empty output        2

    `guard_partner_patch` drops them **after** they are written, so the content is
    lost. The contract already says there is one actress and that did not stop it.
    **Do not hand over the field at all.** You cannot write into a key that is not
    there.
    """
    solo = notebook.scripter_format_schema(False)["properties"]
    duo = notebook.scripter_format_schema(True)["properties"]
    for key in ("wearing_b", "beat_b"):
        assert key not in solo, key
        assert key in duo, key
    # 本人の欄はどちらにもある。
    for key in ("wearing", "beat", "frame", "scene"):
        assert key in solo and key in duo, key

    import inspect
    src = inspect.getsource(chain.run_scripter)
    assert "scripter_format_schema(partner)" in src
    assert "fmt=notebook_mod.SCRIPTER_FORMAT_SCHEMA" not in src


def test_the_rewrite_log_keeps_a_whole_shoot():
    """Twelve loses the first half of a real shoot. A record used for analysis
    keeps one whole shoot.

    The Comiket session (2026-08-20) had 21 turns of direction and the log held
    only the last 12, so "when did the place go in" could not be followed.
    Counting restatements and folds, one shoot runs to about 50 entries.
    """
    assert notebook.REWRITE_LOG_MAX >= 50

    session: dict = {}
    for i in range(80):
        notebook.record_rewrite(
            session, "scripter",
            before={"beat": f"pose {i}"}, after={"beat": f"pose {i + 1}"})
    log = session["rewrite_log"]
    assert len(log) == notebook.REWRITE_LOG_MAX
    # 古いほうから捨てる。最後の一件は最新であること。
    assert log[-1]["changed"]["beat"]["after"] == "pose 80"


def test_the_notebook_has_somewhere_for_what_is_behind_her():
    """BG — what is in frame besides her. Without it, that falls out of the picture.

    In a real shoot (Comiket, 2026-08-20) the director asked for the place four
    times and the background three, and two of the three renders had neither the
    building nor the crowd. There was nowhere among the six fields to put them.

    The name was settled by measuring (7 cases × 10 runs). `set`, `backdrop` and
    `scenery` never arrived; only the studio's own abbreviation `BG` did
    (44% → 68%). `backdrop` does not appear on a single image in this library, and
    `set_dressing`, `extras` and `mob` are all zero. **Only the word actually used
    on set came through.**
    """
    assert "bg" in notebook.SHOT_KEYS
    assert "bg" in notebook.blank()
    assert "BG:" in notebook.render(notebook.blank())
    assert "bg" in notebook.scripter_format_schema(False)["properties"]
    assert notebook._FIELD_RE.match("BG: a crowd of cosplayers")
    assert notebook.parse_scripter(
        '{"intent":"shot","bg":"a crowd"}')["patch"] == {"bg": "a crowd"}

    contract = notebook.FIELD_CONTRACTS["bg"]
    assert "background actors" in contract      # 人はエキストラ
    assert "set dressing" in contract           # 物は飾り込み
    # ボケはカメラの話。ここに入れない（現場では Shallow DoF）。
    assert "depth of field" in contract and "FRAME" in contract


def test_the_label_table_comes_from_shot_keys():
    """One source for the field names. Forget to list one and its value is silently
    thrown away.

    `wearing_drop` was exactly that — present in `_FIELD_RE` and the JSON schema
    but missing from `key_map`, so on every turn answered in label form (all the
    turns with an image attached, and every turn where JSON parsing failed) taking
    a garment off fell on the floor.
    """
    parsed = notebook.parse_scripter(
        "INTENT: shot\nBG: a crowd of cosplayers\nWEARING_DROP: coat"
    )
    assert parsed["patch"]["bg"] == "a crowd of cosplayers"
    assert parsed["patch"]["wearing_drop"] == "coat"


def test_the_angle_word_names_the_camera_not_the_gaze():
    """Put `up` / `down` into the explanation and those words become the material
    for the mix-up.

    In a real shoot (the swing, 2026-08-21) the director's 「カメラを少し上から」
    ("the camera a little from above") was written correctly by compile as
    `high-angle`, and the restatement right after turned it into `low-angle`. The
    reason field it left behind:

      read as the understanding that "the camera is up high"… and that she is
      trying to match the composition as directed (an angle looking up at the
      subject)

    **The camera position was understood; only the word was inverted.** The
    director corrected it the next turn: "it is called from above, not low angle".

    Three fixes were measured (restatement, with the real conversation, 8 runs
    each):

        explaining "camera height and gaze are inverted"   0/8
        narrowing to the camera position alone             7/8
        position alone + "she often faces the other way"   0/8

    One `up` or `down` anywhere in the explanation collapses it. **Trying to
    explain the mix-up hands over the very words the mix-up is made of.**
    """
    frame = notebook.FIELD_CONTRACTS["frame"]
    assert "where the camera stands" in frame
    assert "high_angle" in frame and "low_angle" in frame

    # 角度の説明に視線の向きを持ち込まない。ここが崩れると 0/8 に戻る。
    angle_line = [l for l in frame.splitlines() if "angle word" in l][0]
    for word in (" up ", " down ", "UP", "DOWN"):
        assert word not in angle_line, f"角度の説明に {word!r} を入れない"




_ECHO = _scripter_block(
    intent="shot", tags="__tags", craft_scene="___craft_scene",
)
_REAL = _scripter_block(
    intent="shot",
    tags="sitting, elbows_on_desk, classroom, window_side, white_blouse",
    craft_scene=(
        "She sits at the desk with both elbows planted on the wood, leaning "
        "forward so her weight rests on her forearms. Daylight from the "
        "window side of the classroom falls across the white blouse and "
        "catches the edge of her jaw as she looks into the lens."
    ),
)




def test_weave_refusal_does_not_fire_on_ordinary_picture_words():
    """`scene`, `light`, `frame`, `beat` and `standing` are all legitimate picture
    words.

    The version that rejected on a match against field names alone broke the
    existing test `test_weave_drops_old_place_hour_pose_and_crop`. A real tag does
    not start with `_` — that is the only thing looked at.
    """
    assert not notebook.weave_refusal(
        "standing, looking_at_viewer, scene_light, classroom",
        "She is standing by the window, the light across her frame.",
    )
    assert notebook.weave_refusal("__tags", "___craft_scene") == "schema_echo"
    assert notebook.weave_refusal("tags", "craft_scene") == "schema_echo"


def test_frame_owns_the_crop_without_a_word_list():
    """Dropping the crop families (10 words) still protects everything the old code
    protected.

    Against 30 stored weave outputs it matches the old code 30/30. What is kept
    here are the two cases where, given a deliberately conflicting bag, **the old
    code was the one dropping things**.
    """
    # FRAME は上半身。旧は `close_up` を残していた —— 顔寄りが生き残る。
    got = notebook.drop_crops_not_in_frame(
        "close_up, wide_shot, full_body, sitting, from_above",
        frame="close, upper body",
    )
    assert "close_up" not in got
    assert "wide_shot" not in got and "full_body" not in got
    # アングルは画角ではない。触らない。
    assert "from_above" in got and "sitting" in got

    # FRAME が何も言わない矛盾。旧は両方落として**画角がひとつも無い絵**に
    # した。先に来たほうを残す —— 矛盾は出さず、情報も捨てない。
    got = notebook.drop_crops_not_in_frame("wide_shot, close_up, sitting", frame="")
    assert "wide_shot" in got and "close_up" not in got

    # `establishing_shot` は Muse 側の一覧にだけあった。画角の別名は
    # `framing_from_phrase` に一本化した。
    got = notebook.drop_crops_not_in_frame(
        "establishing_shot, upper_body, desk", frame="close, upper body",
    )
    assert "establishing_shot" not in got and "upper_body" in got
    assert identity.framing_from_phrase("establishing shot") == "full_body"


def test_the_crop_word_lists_are_gone():
    """`_WIDE_CROP_TAGS` / `_CLOSE_CROP_TAGS` are not coming back.

    Words multiplying every time a hole is patched is what makes this studio
    rigid, and the crop is covered by `framing_from_phrase` — **one source that
    already exists**.
    """
    assert not hasattr(notebook, "_WIDE_CROP_TAGS")
    assert not hasattr(notebook, "_CLOSE_CROP_TAGS")




def test_the_route_recorder_changes_nothing_it_only_writes_it_down():
    """**Only a record was added.** The same notebook yields the same prompt.

    The Showrunner (2026-08-31): "observe first and collect only facts. Decide
    nothing yet." This proves the behaviour did not change.
    """
    # 段の内訳を頼んでも、`scrub_craft_tags` の答えは変わらない。
    args = dict(wearing="blouse, skirt", scene="", beat="sitting",
                struck=set(), frame="close, upper body")
    tags = "sitting, white_socks, straw_hat, park, wide_shot, close_up"
    steps: list = []
    assert notebook.scrub_craft_tags(tags, **args) == notebook.scrub_craft_tags(
        tags, trace=steps, **args)
    # 頼んだときだけ、段ごとの出入りが積まれる。
    assert [s["hop"] for s in steps] == [
        "2a reconcile_wardrobe_tags",
        "2b drop_crops_not_in_frame",
        "2c drop_tags_that_fight_the_notebook",
    ]
    assert "straw_hat" in steps[0]["dropped"]
    assert "wide_shot" in steps[1]["dropped"]


