"""Composing a query vector so a search means "X, but away from Y"."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.ai.vecmath import normalize, subtract_concept, vec_sub


def _cos(a, b):
    na, nb = normalize(a), normalize(b)
    return sum(x * y for x, y in zip(na, nb))


def test_normalize_gives_unit_length():
    out = normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in out)), 1.0)


def test_normalize_tolerates_a_zero_vector():
    assert normalize([0.0, 0.0]) == [0.0, 0.0]


def test_subtraction_moves_away_from_the_concept():
    """The whole point: the result must be less like the thing subtracted."""
    base = [1.0, 1.0, 0.0]        # "library with a person in it"
    concept = [0.0, 1.0, 0.0]     # "a person"
    out = subtract_concept(base, concept, 1.0)
    assert _cos(out, concept) < _cos(base, concept)


def test_subtraction_keeps_what_the_concept_does_not_cover():
    base = [1.0, 1.0, 0.0]
    concept = [0.0, 1.0, 0.0]
    scene_only = [1.0, 0.0, 0.0]
    out = subtract_concept(base, concept, 1.0)
    assert _cos(out, scene_only) > _cos(base, scene_only)


def test_strength_zero_is_a_no_op_direction():
    base = [1.0, 2.0, 3.0]
    out = subtract_concept(base, [0.0, 1.0, 0.0], 0.0)
    assert math.isclose(_cos(out, base), 1.0, abs_tol=1e-9)


def test_stronger_subtraction_moves_further():
    base = [1.0, 1.0, 0.0]
    concept = [0.0, 1.0, 0.0]
    weak = subtract_concept(base, concept, 0.3)
    strong = subtract_concept(base, concept, 1.0)
    assert _cos(strong, concept) < _cos(weak, concept)


def test_result_is_unit_length():
    out = subtract_concept([1.0, 1.0, 0.0], [0.0, 1.0, 0.0], 1.0)
    assert math.isclose(math.sqrt(sum(x * x for x in out)), 1.0)


def test_mismatched_or_empty_inputs_degrade_to_the_base():
    assert subtract_concept([1.0, 0.0], [1.0, 0.0, 0.0], 1.0) == normalize([1.0, 0.0])
    assert subtract_concept([], [1.0], 1.0) == []


def test_vec_sub_is_elementwise():
    assert vec_sub([3.0, 5.0], [1.0, 2.0]) == [2.0, 3.0]
