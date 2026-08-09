"""Character preset asset integrity + deterministic mapping.

The asset is authored by hand, so these tests are its quality gate: a preset
that regresses to template filler (python reprs in prose, nested inner lists,
appearance that only echoes the tags) fails here.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.characters.presets import (
    GALLERY_LIMIT,
    preset_label,
    _with_candidate,
    load_seed_presets,
    normalise_gallery,
    personality_text_from_preset,
    plan_reset,
    preset_point_id,
    preset_summary,
    preset_to_character,
    preset_version,
    reload_seed_presets,
    reset_presets_to_defaults,
    seed_point_ids,
    seed_presets_if_empty,
    sync_muse_presets_from_asset,
)
from app.tags.body import AGE_TAGS, BODY_SLOTS, BREAST_TAGS, filter_body_tags
from app.tags.split_tags import soft_normalize_tag

PRESETS = reload_seed_presets()

_JA = re.compile(r"[ぁ-んァ-ヶ一-龥]")
IDENTITY_BUCKETS = (
    "hair_color", "hair_style", "eyes", "body",
    "ears_tails_wings", "favorite_clothes", "footwear",
)
REQUIRED_TAG_BUCKETS = IDENTITY_BUCKETS + (
    "expression", "headwear_accessory", "hobby_actions",
)


def test_every_bundled_muse_has_a_non_negative_version():
    """Admin sync compares asset version to Qdrant; missing version is treated
    as older than 0, but the file itself must declare it explicitly."""
    for preset in PRESETS:
        assert "version" in preset, preset.get("id")
        assert preset_version(preset) >= 0
        assert isinstance(preset["version"], int)


def test_asset_loads_with_unique_ids():
    # Thirty written properly beat a hundred that read like a spreadsheet.
    assert len(PRESETS) >= 30
    keys = [p["id"] for p in PRESETS]
    assert len(set(keys)) == len(keys)
    assert len({preset_point_id(k) for k in keys}) == len(keys)


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_preset_schema(preset):
    for field in (
        "id", "name", "name_ja", "summary", "summary_ja",
        "gender", "subject_tag", "signature_prop",
    ):
        assert str(preset.get(field) or "").strip(), f"{preset['id']}: {field} is empty"

    # English is the primary text; *_ja carries the Japanese.
    assert not _JA.search(preset["name"]), f"{preset['id']}: name must be English"
    assert not _JA.search(preset["summary"]), f"{preset['id']}: summary must be English"
    assert _JA.search(preset["name_ja"]), f"{preset['id']}: name_ja must be Japanese"
    assert _JA.search(preset["summary_ja"]), f"{preset['id']}: summary_ja must be Japanese"

    # Template filler that broke the previous asset.
    for field in ("summary", "summary_ja"):
        text = preset[field]
        assert "['" not in text and "']" not in text, f"{preset['id']}: {field} leaks a repr"
    assert "性格は" not in preset["summary_ja"], f"{preset['id']}: templated summary_ja"

    for field in ("personality", "inner", "inner_ja"):
        values = preset.get(field)
        assert isinstance(values, list) and values, f"{preset['id']}: {field} empty"
        assert all(isinstance(v, str) and v.strip() for v in values), (
            f"{preset['id']}: {field} must be a flat list of non-empty strings"
        )
    assert len(preset["personality"]) >= 3
    assert len(preset["inner"]) >= 2
    assert len(preset["inner"]) == len(preset["inner_ja"])
    assert all(not _JA.search(v) for v in preset["inner"]), f"{preset['id']}: inner must be English"
    assert all(_JA.search(v) for v in preset["inner_ja"]), f"{preset['id']}: inner_ja must be Japanese"

    appearance = preset.get("appearance") or {}
    for key in ("hair", "eyes", "expression", "body"):
        text = str(appearance.get(key) or "").strip()
        assert text, f"{preset['id']}: appearance.{key} is empty"
        assert not _JA.search(text), f"{preset['id']}: appearance.{key} must be English"
    # Prose must add something the tags do not already say.
    hair_tag = (preset["tags"].get("hair_color") or [""])[0].replace("_", " ")
    assert not appearance["hair"].strip().lower().startswith(hair_tag.lower()), (
        f"{preset['id']}: appearance.hair only echoes the tag"
    )

    tags = preset.get("tags") or {}
    for bucket in REQUIRED_TAG_BUCKETS:
        assert bucket in tags, f"{preset['id']}: tags.{bucket} missing"
        assert isinstance(tags[bucket], list)
        assert all(isinstance(t, str) and t.strip() for t in tags[bucket])
    for bucket in ("hair_color", "eyes", "favorite_clothes", "expression", "hobby_actions"):
        assert tags[bucket], f"{preset['id']}: tags.{bucket} must not be empty"

    prefs = preset.get("preferences") or {}
    for key in ("likes", "dislikes", "favorite_colors"):
        assert prefs.get(key), f"{preset['id']}: preferences.{key} is empty"
        assert all(isinstance(v, str) for v in prefs[key])

    scene = preset.get("default_scene") or {}
    assert str(scene.get("outfit_style") or "").strip()
    assert scene.get("vibe_keywords")


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_preset_maps_to_character(preset):
    ch = preset_to_character(preset)

    assert ch["identity_tags"], preset["id"]
    assert ch["source"] == "preset"
    # Count is a fact about the scene, not about her. Locked into identity it
    # said "one girl" in every prompt and made a second character impossible.
    subject = soft_normalize_tag(preset["subject_tag"])
    assert subject not in ch["identity_tags"], (
        f"{preset['id']}: {subject} must not be locked to the character"
    )
    assert ch["subject_tag"] == subject, preset["id"]

    # Expression and gesture are per-panel performance — never baked into identity.
    for tag in (preset["tags"].get("expression") or []):
        assert soft_normalize_tag(tag) not in ch["identity_tags"], (
            f"{preset['id']}: expression {tag} leaked into identity"
        )
    for tag in (preset["tags"].get("hobby_actions") or []):
        assert soft_normalize_tag(tag) not in ch["identity_tags"], (
            f"{preset['id']}: gesture {tag} leaked into identity"
        )
    assert ch["expression_vocab"], preset["id"]
    assert ch["gesture_vocab"], preset["id"]

    # P5 invariant: identity and prop layers stay disjoint.
    assert not (set(ch["identity_tags"]) & set(ch["prop_tags"])), preset["id"]
    # The authored carry prop is what the story threads through the panels.
    assert ch["signature_prop"] == soft_normalize_tag(preset["signature_prop"]), preset["id"]
    assert ch["signature_prop"] in ch["prop_tags"]
    assert ch["signature_prop"] not in ch["identity_tags"]

    personality = ch["personality"]
    assert personality["summary"] and personality["summary_ja"]
    assert personality["inner"] and personality["traits"]
    assert personality["preset_key"] == preset["id"]
    assert all(isinstance(v, str) for v in personality["inner"])


def test_personality_text_prefers_locale():
    preset = PRESETS[0]
    ja = personality_text_from_preset(preset, locale="ja")
    en = personality_text_from_preset(preset, locale="en")
    assert preset["summary_ja"] in ja
    assert preset["summary"] in en
    # Appearance prose is English in both.
    assert preset["appearance"]["hair"] in ja


def test_summary_row_is_light():
    row = preset_summary(PRESETS[0])
    assert set(row) == {
        "id", "preset_key", "slug", "name", "name_ja", "title", "title_ja",
        "summary", "summary_ja", "charm_ja",
        "gender", "subject_tag", "traits", "tag_count", "board", "gallery",
        "hair_color", "eye_color", "user_created", "diary_unread_count",
    }
    assert row["tag_count"] > 0


def test_bundled_preset_has_an_empty_board():
    """A shipped character has no reference images until someone draws them."""
    row = preset_summary(PRESETS[0])
    assert row["board"] == {"sheet": "", "portrait": ""}
    assert row["user_created"] is False


def test_summary_carries_board_images_once_drawn():
    row = preset_summary({**PRESETS[0], "board": {"sheet": "abc123"}, "user_created": True})
    assert row["board"] == {"sheet": "abc123", "portrait": ""}
    assert row["user_created"] is True


# ── the artwork survives a re-seed ──────────────────────────────────────────
def test_a_preset_id_is_stable_across_reseeds():
    """This is what lets the asset be re-read without dropping the collection.
    Dropping it took every character's portrait with it — one run left all 100
    with no face, because a preset's pictures live on the preset."""
    for preset in PRESETS[:5]:
        assert preset_point_id(preset["id"]) == preset_point_id(preset["id"])
    ids = {preset_point_id(p["id"]) for p in PRESETS}
    assert len(ids) == len(PRESETS), "two characters sharing a row would overwrite"


def test_a_re_roll_keeps_the_earlier_candidates():
    """The fifth attempt is not automatically better than the second, and only
    the user can say which face is hers."""
    kept = []
    for sha in ("aaa", "bbb", "ccc"):
        kept = _with_candidate(kept, sha, "W.json")
    assert [c["sha"] for c in kept] == ["ccc", "bbb", "aaa"], "newest first"


def test_a_candidate_remembers_which_checkpoint_drew_it():
    """Drawing one character on two models to compare them only works if you
    can tell the results apart."""
    kept = _with_candidate([], "aaa", "API_Anima_RIN.json")
    kept = _with_candidate(kept, "bbb", "API_Anima_Ribeya.json")
    assert [c["workflow"] for c in kept] == ["API_Anima_Ribeya.json", "API_Anima_RIN.json"]
    assert kept[0]["at"] >= kept[1]["at"]


def test_drawing_the_same_image_twice_does_not_duplicate_it():
    kept = _with_candidate([{"sha": "aaa", "workflow": "", "at": 1.0},
                            {"sha": "bbb", "workflow": "", "at": 0.0}], "bbb", "W.json")
    assert [c["sha"] for c in kept] == ["bbb", "aaa"]


def test_the_candidate_list_has_an_end():
    kept = []
    for i in range(GALLERY_LIMIT + 5):
        kept = _with_candidate(kept, f"sha{i}", "W.json")
    assert len(kept) == GALLERY_LIMIT
    assert kept[0]["sha"] == f"sha{GALLERY_LIMIT + 4}", "the newest is kept"


def test_the_old_flat_portrait_list_still_reads():
    """The first version could not say which checkpoint drew a candidate."""
    out = normalise_gallery(["aaa", "bbb"])
    assert [c["sha"] for c in out["portrait"]] == ["aaa", "bbb"]
    assert out["sheet"] == []


def test_candidates_are_kept_per_slot():
    out = normalise_gallery({"sheet": [{"sha": "s1", "workflow": "W.json", "at": 3}],
                             "portrait": [{"sha": "p1", "workflow": "V.json", "at": 4}]})
    assert out["sheet"][0]["workflow"] == "W.json"
    assert out["portrait"][0]["sha"] == "p1"


def test_the_summary_carries_the_colours_the_gallery_filters_by():
    """Hair and eye colour are what a person searching a hundred characters
    has in mind, and they were buried inside `tags`."""
    for preset in PRESETS:
        row = preset_summary(preset)
        assert row["hair_color"], preset["id"]
        assert row["eye_color"], preset["id"]


def test_no_age_tag_reaches_a_character():
    """Age is written setting, never a tag.

    `mature_female` on a character written as a student rendered a woman two
    decades older, in every picture she was ever in, with nothing downstream
    able to remove it. The bucket is allowlisted now — this is the guard that
    says so out loud.
    """
    for preset in PRESETS:
        identity = preset_to_character(preset)["identity_tags"]
        leaked = sorted(set(identity) & AGE_TAGS)
        assert not leaked, f"{preset['id']}: age tag in identity: {leaked}"


def test_the_asset_itself_names_no_age():
    """Refusing the tag at the mapping is not enough — the asset should not
    carry one either, or the next reader copies it into a new preset."""
    for preset in PRESETS:
        for bucket, values in (preset.get("tags") or {}).items():
            found = sorted({soft_normalize_tag(str(v)) for v in values or []} & AGE_TAGS)
            assert not found, f"{preset['id']}: tags.{bucket} carries {found}"


def test_body_bucket_is_allowlisted_not_denylisted():
    """A build the vocabulary does not know is dropped, not locked in."""
    kept, refused = filter_body_tags(["tall", "mature_female", "wearing_a_hat"])
    assert kept == ["tall"]
    assert refused == ["mature_female", "wearing_a_hat"]


# The gap between how she reads and what she actually is. A picture of a
# composed face is not worth drawing; the half-second the composure slips is.
_BODY_WORDS = ("耳", "頬", "ほっぺ", "目", "口", "手", "指", "肩", "声",
               "足", "首", "背中", "膝", "髪", "顔", "まつげ")
# Faces that are not the composed one. At least one, or the gap cannot be drawn.
_SLIPPING = {
    "blush", "embarrassed", "pout", "wavering_eyes", "surprised", "happy",
    "sleepy", "light_smile", "smile", "grin", "flustered", "tearing_up", "shy",
}


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_every_character_has_a_name_and_a_title(preset):
    """`name_ja` used to be a description — 「クールな先輩」 — which left the
    panel with nothing to call anybody. The name is a name; the title is what
    she is known for."""
    for field in ("title", "title_ja"):
        assert str(preset.get(field) or "").strip(), f"{preset['id']}: {field} empty"
    assert _JA.search(preset["title_ja"]), f"{preset['id']}: title_ja must be Japanese"
    assert not _JA.search(preset["title"]), f"{preset['id']}: title must be English"
    assert preset["name_ja"] != preset["title_ja"], preset["id"]
    # A person's name, not a label: 「白瀬 みなも」 not 「静かな写真部」. Family
    # name, a space, given name — a description does not have that shape, and
    # the ending is no help here because 「せな」 and 「わかな」 are names too.
    name = preset["name_ja"]
    family, _, given = name.replace("　", " ").partition(" ")
    assert family and given, f"{preset['id']}: name_ja '{name}' has no given name"
    assert len(name) <= 10, f"{preset['id']}: name_ja '{name}' reads as a description"


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_the_hidden_charm_is_something_a_picture_can_show(preset):
    """It reaches the acting seat as HIDDEN CHARM and is supposed to become a
    face. An abstract one ('she is lonely inside') cannot be drawn and quietly
    does nothing."""
    for field in ("charm", "charm_ja"):
        assert str(preset.get(field) or "").strip(), f"{preset['id']}: {field} empty"
    assert not _JA.search(preset["charm"]), f"{preset['id']}: charm must be English"
    charm = preset["charm_ja"]
    assert _JA.search(charm), f"{preset['id']}: charm_ja must be Japanese"
    assert any(w in charm for w in _BODY_WORDS), (
        f"{preset['id']}: charm_ja names no part of her: {charm[:40]}"
    )

    faces = set(preset["tags"]["expression"])
    assert faces & _SLIPPING, (
        f"{preset['id']}: no face for the gap to land on: {sorted(faces)}"
    )


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_the_writing_is_thick_enough_to_want_to_draw(preset):
    """The old asset averaged 25 characters of summary and two lines of inner
    life, which reads as a spreadsheet row rather than a person."""
    assert len(preset["summary_ja"]) >= 85, (
        f"{preset['id']}: summary_ja is {len(preset['summary_ja'])} chars"
    )
    assert len(preset["personality"]) >= 6, preset["id"]
    assert len(preset["inner_ja"]) >= 3, preset["id"]
    ap = preset["appearance"]
    for key in ("voice", "habit", "first_impression"):
        assert str(ap.get(key) or "").strip(), f"{preset['id']}: appearance.{key} empty"
    assert str((preset["default_scene"] or {}).get("signature_moment") or "").strip(), (
        f"{preset['id']}: no signature_moment"
    )


def test_the_roster_does_not_look_like_one_character_thirty_times():
    hair = {p["tags"]["hair_color"][0] for p in PRESETS}
    eyes = {p["tags"]["eyes"][0] for p in PRESETS}
    assert len(hair) >= 15, sorted(hair)
    assert len(eyes) >= 12, sorted(eyes)
    assert len({p["name_ja"] for p in PRESETS}) == len(PRESETS)


def test_ids_are_sequential_and_the_readable_key_survives():
    """A descriptive id reads well right up until a character is rewritten and
    it describes who she used to be — and renaming one moves her Qdrant point
    and orphans whatever has been drawn for her."""
    assert [p["id"] for p in PRESETS] == [f"c{n:03d}" for n in range(1, len(PRESETS) + 1)]
    slugs = [p["slug"] for p in PRESETS]
    assert all(slugs), "the readable key is what a log line needs"
    assert len(set(slugs)) == len(slugs)
    assert all(s == s.lower() and " " not in s for s in slugs)


def test_a_log_line_still_says_who_she_is():
    label = preset_label(PRESETS[13])
    assert PRESETS[13]["id"] in label
    assert PRESETS[13]["slug"] in label
    # A preset without one degrades to the id rather than to "None".
    assert preset_label({"id": "c999"}) == "c999"
    assert preset_label({}) == "?"


_BREAST = set(BREAST_TAGS)


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_every_character_has_a_figure(preset):
    """The body-slot lock only works if something is in the slot. With the chest
    bucket empty — which it was for all thirty — `conflicting_body_tags` returns
    nothing and the whole mechanism idles."""
    body = preset["tags"]["body"]
    chest = [t for t in body if t in _BREAST]
    assert len(chest) == 1, f"{preset['id']}: chest tags {chest}"


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_one_tag_per_body_slot(preset):
    """Two from one slot leaves both 'present', so neither bans the other and
    the lock quietly stops working."""
    body = preset["tags"]["body"]
    for slot in BODY_SLOTS:
        hit = [t for t in body if t in slot]
        assert len(hit) <= 1, f"{preset['id']}: {hit} are the same measurement"


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_the_roster_avoids_the_builds_that_overshoot(preset):
    """`petite` renders her markedly smaller than her sheet says. The extremes
    at the other end are what `opposing_negative` already pushes against, so
    authoring one would fight our own machinery."""
    body = set(preset["tags"]["body"])
    assert "petite" not in body, preset["id"]
    assert not (body & {"huge_breasts", "gigantic_breasts"}), preset["id"]


def test_the_roster_is_not_one_figure_thirty_times():
    chest = [t for p in PRESETS for t in p["tags"]["body"] if t in _BREAST]
    assert len(set(chest)) >= 3, sorted(set(chest))
    assert max(chest.count(c) for c in set(chest)) <= len(PRESETS) * 0.5


# ── what a reset actually resets ────────────────────────────────────────────
class _FakeQC:
    """Enough Qdrant to run the seed / reset path against."""

    def __init__(self, points: dict | None = None):
        self.points: dict[str, dict] = dict(points or {})

    async def scroll(self, *, collection_name, limit=200, offset=None,
                     with_payload=True, with_vectors=False, scroll_filter=None):
        items = sorted(self.points.items())
        start = int(offset or 0)
        chunk = items[start:start + limit]
        nxt = start + limit if start + limit < len(items) else None
        return [SimpleNamespace(id=pid, payload=p if with_payload else None)
                for pid, p in chunk], nxt

    async def upsert(self, *, collection_name, points):
        for p in points:
            self.points[str(p.id)] = dict(p.payload or {})

    async def delete(self, *, collection_name, points_selector):
        for pid in points_selector.points:
            self.points.pop(str(pid), None)


class _FakeDB:
    def __init__(self, points: dict | None = None):
        self._qc = _FakeQC(points)

    async def ensure_character_presets_collection(self):
        return None


def _run(coro):
    return asyncio.run(coro)


def _legacy_row(key: str, **extra) -> dict:
    """A row from the roster before the ids were renumbered: the descriptive
    key sat in `id`, and there was no `slug`."""
    return {"id": key, "name": key, **extra}


BOARD = {"sheet": "sheet_sha", "portrait": "portrait_sha"}


def test_a_character_the_file_no_longer_claims_is_removed():
    """The bug this is here for: renumbering the roster (`darkroom_photo` →
    `c007`) moved every point id, so re-seeding wrote 30 new rows *beside* the
    previous 100 instead of replacing them — 130 characters, 100 of which no UI
    could name and no reset could reach."""
    legacy = {preset_point_id(k): _legacy_row(k)
              for k in ("shrine_maiden", "night_bakery", "library_cat")}
    db = _FakeDB(legacy)

    result = _run(reset_presets_to_defaults(db, vector_dim=4))

    assert result["removed"] == 3
    assert result["inserted"] == len(PRESETS)
    assert set(db._qc.points) == seed_point_ids()


def test_a_character_you_wrote_yourself_survives_a_reset():
    mine = {"11111111-2222-3333-4444-555555555555":
            {"id": "user-abc", "name": "Mine", "user_created": True}}
    db = _FakeDB(mine)

    result = _run(reset_presets_to_defaults(db, vector_dim=4))

    assert result["kept"] == 1
    assert "11111111-2222-3333-4444-555555555555" in db._qc.points
    assert len(db._qc.points) == len(PRESETS) + 1


def test_wipe_is_the_way_back_to_only_the_shipped_roster():
    db = _FakeDB({"11111111-2222-3333-4444-555555555555":
                  {"id": "user-abc", "user_created": True}})

    result = _run(reset_presets_to_defaults(db, vector_dim=4, wipe=True))

    assert result["kept"] == 0
    assert result["removed"] == 1
    assert set(db._qc.points) == seed_point_ids()


def test_a_dry_run_reports_the_same_numbers_and_changes_nothing():
    """The confirm dialog says "and these 100 go" only because it asked."""
    legacy = {preset_point_id(k): _legacy_row(k, board=dict(BOARD))
              for k in ("shrine_maiden", "night_bakery")}
    db = _FakeDB(legacy)

    plan = _run(reset_presets_to_defaults(db, vector_dim=4, dry_run=True))

    assert plan["dry_run"] is True
    assert plan["removed"] == 2
    assert plan["seeds"] == len(PRESETS)
    assert plan["orphan_images"] == 2, "both slots of one board are the same shas"
    assert sorted(plan["removed_labels"]) == ["night_bakery", "shrine_maiden"]
    assert db._qc.points == legacy, "a preview must not touch the collection"


def test_her_own_pictures_survive_a_re_seed():
    """Dropping the collection took every portrait with it. Writing rows over in
    place must not quietly do the same thing."""
    point_id = preset_point_id(PRESETS[0]["id"])
    db = _FakeDB({point_id: {**PRESETS[0], "board": dict(BOARD)}})

    _run(reset_presets_to_defaults(db, vector_dim=4))

    assert db._qc.points[point_id]["board"] == BOARD


def test_her_diaries_survive_a_re_seed():
    point_id = preset_point_id(PRESETS[0]["id"])
    diary = [{"id": "d1", "summary_ja": "褒められた", "content_ja": "秘密", "read": False}]
    db = _FakeDB({point_id: {**PRESETS[0], "diaries": diary, "board": dict(BOARD)}})

    _run(reset_presets_to_defaults(db, vector_dim=4))

    assert db._qc.points[point_id]["diaries"] == diary
    assert db._qc.points[point_id]["board"] == BOARD


def test_muse_sync_updates_only_when_asset_version_is_newer():
    point_id = preset_point_id(PRESETS[0]["id"])
    diary = [{"id": "keep-me", "content_ja": "消さないで"}]
    stale = {
        **PRESETS[0],
        "version": -1,  # treated older than asset 0… use missing instead
        "name_ja": "古い名前",
        "diaries": diary,
        "board": dict(BOARD),
    }
    del stale["version"]  # pre-version installs
    db = _FakeDB({point_id: stale})

    preview = _run(sync_muse_presets_from_asset(db, vector_dim=4, dry_run=True))
    assert preview["updated"] == 1
    assert preview["inserted"] == len(PRESETS) - 1
    assert db._qc.points[point_id]["name_ja"] == "古い名前"

    result = _run(sync_muse_presets_from_asset(db, vector_dim=4))
    assert result["updated"] == 1
    assert result["inserted"] == len(PRESETS) - 1
    row = db._qc.points[point_id]
    assert row["name_ja"] == PRESETS[0]["name_ja"]
    assert row["version"] == PRESETS[0]["version"]
    assert row["diaries"] == diary
    assert row["board"] == BOARD

    again = _run(sync_muse_presets_from_asset(db, vector_dim=4))
    assert again["updated"] == 0
    assert again["inserted"] == 0
    assert again["skipped"] == len(PRESETS)


def test_muse_sync_does_not_delete_stale_bundled_rows():
    """Unlike reset, sync never removes anyone — only inserts/updates by version."""
    legacy_id = preset_point_id("shrine_maiden")
    db = _FakeDB({legacy_id: _legacy_row("shrine_maiden", diaries=[{"id": "x"}])})

    result = _run(sync_muse_presets_from_asset(db, vector_dim=4))

    assert legacy_id in db._qc.points
    assert db._qc.points[legacy_id]["diaries"] == [{"id": "x"}]
    assert result["inserted"] == len(PRESETS)


def test_pictures_follow_a_character_whose_id_was_renumbered():
    """The rescue that stops rule 2 from becoming the next version of the bug:
    her slug is the name that survives a renumbering, so her board is claimed
    off the row being deleted rather than deleted with it."""
    seed = PRESETS[0]
    old_id = preset_point_id(seed["slug"])         # when the slug *was* the id
    db = _FakeDB({old_id: _legacy_row(seed["slug"], board=dict(BOARD))})

    result = _run(reset_presets_to_defaults(db, vector_dim=4))

    assert result["carried_over"] == 1
    assert old_id not in db._qc.points
    assert db._qc.points[preset_point_id(seed["id"])]["board"] == BOARD


def test_an_edit_to_a_bundled_character_is_what_a_reset_undoes():
    point_id = preset_point_id(PRESETS[0]["id"])
    db = _FakeDB({point_id: {**PRESETS[0], "name_ja": "書き換えた名前"}})

    _run(reset_presets_to_defaults(db, vector_dim=4))

    assert db._qc.points[point_id]["name_ja"] == PRESETS[0]["name_ja"]


def test_seeding_an_empty_collection_still_only_runs_once():
    db = _FakeDB()
    assert _run(seed_presets_if_empty(db, vector_dim=4)) == len(PRESETS)
    assert _run(seed_presets_if_empty(db, vector_dim=4)) == 0
    assert len(db._qc.points) == len(PRESETS)


def test_the_plan_is_readable_without_a_database():
    stored = {
        preset_point_id(PRESETS[0]["id"]): dict(PRESETS[0]),   # current
        preset_point_id("shrine_maiden"): _legacy_row("shrine_maiden"),  # stale
        "11111111-2222-3333-4444-555555555555": {"id": "user-a", "user_created": True},
    }
    plan = plan_reset(stored)
    assert plan["refreshed"] == 1
    assert plan["stale"] == [preset_point_id("shrine_maiden")]
    assert plan["kept"] == 1
    assert plan["removed"] == plan["stale"]


# ── what the picker filters by ──────────────────────────────────────────────
def test_every_trait_reaches_the_picker():
    """The row used to carry `personality[:5]`. The picker filters and searches
    on it, so the cap made two of every character's seven traits unfindable —
    and left ten of the thirty unreachable by any trait chip at all."""
    for preset in PRESETS:
        assert preset_summary(preset)["traits"] == preset["personality"]


def test_a_trait_chip_can_reach_most_of_the_roster():
    """The picker only offers a trait three or more characters share — a trait
    one character has is a name, not a filter. That threshold is only useful if
    the shared traits cover most of the roster."""
    from collections import Counter

    rows = [preset_summary(p) for p in PRESETS]
    counts = Counter(t for r in rows for t in r["traits"])
    offered = {t for t, n in counts.items() if n >= 3}
    reachable = [r for r in rows if set(r["traits"]) & offered]
    assert len(offered) >= 10, sorted(offered)
    assert len(reachable) >= len(rows) * 0.8, f"{len(reachable)}/{len(rows)}"


def test_every_colour_on_the_roster_has_a_swatch():
    """The picker filters by colour as a colour — a dot you glance at rather
    than a word you read. `colorSwatch.js` held plain colour names only, so
    `chestnut_hair`, `pale_blue_eyes` and nine others rendered the same
    fallback grey: a third of the roster, all one indistinguishable dot.

    Parsed rather than imported because it is the frontend's table and there is
    no JS test runner here; the point is to fail when a new character is
    authored in a colour the swatches cannot draw.
    """
    src = (Path(__file__).parent.parent.parent
           / "frontend/src/components/muse/colorSwatch.js").read_text(encoding="utf-8")
    bases = set(re.findall(r"^  (\w+): '#", src, re.M))
    modifiers = set(re.findall(r"(\w+): -?[\d.]+",
                               re.findall(r"SHADE = \{([^}]+)\}", src)[0]))

    def base_of(tag: str, noun: str) -> str:
        word = tag.removesuffix(f"_{noun}")
        head, _, rest = word.partition("_")
        return rest if rest and head in modifiers else word

    unpaintable = set()
    for preset in PRESETS:
        for tag, noun in ((preset["tags"]["hair_color"][0], "hair"),
                          (preset["tags"]["eyes"][0], "eyes")):
            if base_of(tag, noun) not in bases:
                unpaintable.add(tag)
    assert not unpaintable, f"no swatch for {sorted(unpaintable)}"
