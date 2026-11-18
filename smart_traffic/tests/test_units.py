"""Unit tests for IoU, association, edge filter and plate validation."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from association import iou, is_edge, associate, decide  # noqa: E402
from plate_reader import normalise, is_valid, rule_correct  # noqa: E402


def test_iou_identical():
    assert abs(iou([0, 0, 10, 10], [0, 0, 10, 10]) - 1.0) < 1e-9


def test_iou_disjoint():
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0


def test_iou_partial():
    assert abs(iou([0, 0, 10, 10], [5, 0, 15, 10]) - 50 / 150) < 1e-9


def test_iou_zero_area():
    assert iou([5, 5, 5, 5], [5, 5, 5, 5]) == 0.0


def test_edge_rider():
    assert is_edge([2, 50, 100, 200], 640, 480)        # cut at left
    assert is_edge([50, 2, 100, 200], 640, 480)        # cut at top (head may be missing)
    assert not is_edge([50, 50, 100, 200], 640, 480)


def test_bottom_cut_rider_is_not_edge():
    # rider approaching the camera: box touches the bottom border, head fully visible
    assert not is_edge([200, 40, 400, 480], 640, 480)


def test_associate_and_decide_violation():
    tracks = [("1", [100, 100, 200, 300], "rider", 0.9),
              ("2", [130, 100, 170, 140], "no_helmet", 0.8),
              ("3", [130, 260, 170, 280], "number_plate", 0.8)]
    r = associate(tracks, 640, 480)["1"]
    assert r["no_helmet"] and r["plate"] and decide(r)


def test_helmet_rider_not_violation():
    tracks = [("1", [100, 100, 200, 300], "rider", 0.9),
              ("2", [130, 100, 170, 140], "helmet", 0.8)]
    assert not decide(associate(tracks, 640, 480)["1"])


def test_edge_rider_not_violation():
    tracks = [("1", [0, 100, 200, 300], "rider", 0.9),
              ("2", [30, 100, 70, 140], "no_helmet", 0.8)]
    assert not decide(associate(tracks, 640, 480)["1"])


def test_plate_normalise_and_validate():
    assert normalise("mh 12-ab 1234") == "MH12AB1234"
    assert is_valid("MH12AB1234")
    assert is_valid("22BH1234AA")
    assert not is_valid("XX12AB1234")
    assert not is_valid("HELLO")


def test_rule_correct():
    assert rule_correct("MH21BWO2I4") == "MH21BW0214"


from notifier import plate_distance, fuzzy_lookup, resolve_plate  # noqa: E402

DB = [{"name": "A", "number_plate_number": "MH21BW0214", "phone_number": "+910000000001"},
      {"name": "B", "number_plate_number": "MH12KX4521", "phone_number": "+910000000002"}]


def test_plate_distance_confusables_cheaper():
    assert plate_distance("MH12KX4S21", "MH12KX4521") == 0.5
    assert plate_distance("MH12KX4X21", "MH12KX4521") == 1.0


def test_fuzzy_lookup_accepts_close_and_rejects_far():
    owner, dist, _ = fuzzy_lookup(["MH21BU021"], DB)
    assert owner["name"] == "A" and dist <= 2
    assert fuzzy_lookup(["GA14C6251"], DB) is None


def test_resolve_plate_prefers_exact_vote():
    readings = [{"raw_ocr": "x", "plate": "MH12KX4521", "valid": True},
                {"raw_ocr": "y", "plate": "MH21BU021", "valid": False}]
    res = resolve_plate(readings, DB)
    assert res["match"] == "exact" and res["plate"] == "MH12KX4521"
