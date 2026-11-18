"""Geometric association of helmet / no_helmet / number_plate boxes to riders."""
import config


def iou(a, b):
    """IoU of two [x1, y1, x2, y2] boxes, safe against division by zero."""
    xa, ya = max(a[0], b[0]), max(a[1], b[1])
    xb, yb = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, xb - xa) * max(0, yb - ya)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def box_area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def is_edge(box, w, h, m=config.EDGE_MARGIN):
    """True when the rider is cut at the left, right or top border, i.e. the head may be
    outside the frame. A cut at the bottom border is allowed: riders approaching the
    camera are clipped at the bottom while the head is still fully visible (v2 rule)."""
    x1, y1, x2, y2 = box
    if config.EDGE_RULE == "all":                      # v1 rule (all four borders)
        return x1 <= m or y1 <= m or x2 >= w - m or y2 >= h - m
    return x1 <= m or y1 <= m or x2 >= w - m


def associate(tracks, frame_w, frame_h):
    """Bind each non-rider object to the rider with the highest IoU.

    Returns {rider_id: {"box", "edge", "helmet", "no_helmet", "plate"}}.
    """
    riders = {tid: {"box": box, "edge": is_edge(box, frame_w, frame_h),
                    "helmet": None, "no_helmet": None, "plate": None,
                    "_best": {}}
              for tid, box, label, _ in tracks if label == "rider"}

    for tid, box, label, conf in tracks:
        if label not in ("helmet", "no_helmet", "number_plate"):
            continue
        best_id, best_iou = None, config.IOU_LINK_THRESHOLD
        for rid, r in riders.items():
            ov = iou(box, r["box"])
            if ov > best_iou:
                best_id, best_iou = rid, ov
        if best_id is None:
            continue
        key = "plate" if label == "number_plate" else label
        prev = riders[best_id]["_best"].get(key)
        cand = (best_iou, box_area(box))
        if prev is None or cand > prev:        # higher IoU wins, larger area breaks ties
            riders[best_id]["_best"][key] = cand
            riders[best_id][key] = box

    for r in riders.values():
        r.pop("_best")
        # Conflict rule (REQ-12): if both helmet and no_helmet bind, keep the one
        # with the larger overlap; ties resolve in favour of helmet (avoid false fines).
    return riders


def decide(rider):
    """Return True when the rider is a helmet violation in this frame."""
    if rider["edge"] or rider["no_helmet"] is None:
        return False
    if rider["helmet"] is not None:
        return iou(rider["no_helmet"], rider["box"]) > iou(rider["helmet"], rider["box"])
    return True
