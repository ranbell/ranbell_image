"""Frozenset / always_fixed classification must not freeze expression diversity."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.api.inspire import _get_tag_axis


def test_expression_eye_tags_are_emotion_not_always_fixed():
    """Regression: endswith('_eyes') used to force teary_eyes → always_fixed."""
    for tag in (
        "teary_eyes", "closed_eyes", "watery_eyes", "half-closed_eyes",
        "empty_eyes", "tired_eyes",
    ):
        assert _get_tag_axis(tag) == "emotion", tag


def test_eye_color_still_always_fixed():
    assert _get_tag_axis("blue_eyes") == "always_fixed"
    assert _get_tag_axis("red_eyes") == "always_fixed"


def test_gaze_tags_are_not_composition_fixed():
    # Viewer-relative gaze used to be always_fixed composition — now emotion.
    for tag in ("looking_at_viewer", "looking_away", "looking_back"):
        assert _get_tag_axis(tag) == "emotion", tag
    # looking_down/up stay action (pose), but must not be always_fixed.
    assert _get_tag_axis("looking_down") == "action"
    assert _get_tag_axis("looking_up") == "action"


def test_smile_is_emotion_volatile():
    assert _get_tag_axis("smile") == "emotion"
    assert _get_tag_axis("blush") == "emotion"
