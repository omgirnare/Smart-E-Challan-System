"""Smart Traffic Safety & Automated Challan System - end-to-end pipeline.

Usage:
    python main.py --source ../assets/test2.mp4
    python main.py --source ../assets/test5.jpeg
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

import cv2

import config
from association import associate, decide, iou
from detector import Detector, Tracker
import notifier
from notifier import build_alert_sms, build_sms, load_db, lookup_owner, resolve_plate, send_sms
from plate_reader import read_plate

COLORS = {"rider": (255, 140, 0), "helmet": (0, 200, 0), "no_helmet": (0, 0, 255),
          "number_plate": (0, 215, 255)}


def draw(frame, tracks, violators):
    for tid, (x1, y1, x2, y2), label, conf in tracks:
        c = COLORS.get(label, (200, 200, 200))
        thick = 3 if label == "rider" and tid in violators else 2
        if label == "rider" and tid in violators:
            c = (0, 0, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), c, thick)
        txt = f"ID {tid} {label}" + (f" {conf:.2f}" if conf else "")
        if label == "rider" and tid in violators:
            txt = f"ID {tid} VIOLATION"
        (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, max(0, y1 - th - 6)), (x1 + tw + 4, y1), c, -1)
        cv2.putText(frame, txt, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)


def run(source, out_dir):
    os.makedirs(os.path.join(out_dir, "riders"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "plates"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "frames"), exist_ok=True)

    detector, tracker = Detector(), Tracker()
    is_image = source.lower().endswith((".jpg", ".jpeg", ".png"))
    cap = None if is_image else cv2.VideoCapture(source)
    if cap is not None and not cap.isOpened():
        raise SystemExit(f"Error: cannot open video {source}")

    if is_image:
        frames_iter = iter([cv2.imread(source)])
        w = h = fps = None
    else:
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        vid_path = os.path.join(out_dir, "output_annotated.mp4")
        # H.264 ('avc1') plays in browsers / the dashboard; fall back to mp4v if unavailable
        writer = cv2.VideoWriter(vid_path, cv2.VideoWriter_fourcc(*"avc1"), fps, (w, h))
        if not writer.isOpened():
            writer = cv2.VideoWriter(vid_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        frames_iter = iter(lambda: cap.read(), None)

    cases = {}              # track_id -> evidence
    alias = {}              # new track id -> case id (after an ID switch)
    counts = {k: 0 for k in config.CLASSES}
    frame_no, infer_times, saved_frames = 0, [], 0
    skipped = {}            # reason -> rider-frames not counted as a violation (shown on dashboard)

    while True:
        if is_image:
            frame = next(frames_iter, None)
            if frame is None:
                break
        else:
            ok, frame = cap.read()
            if not ok:
                break
        frame_no += 1
        if frame_no % 10 == 0 or frame_no == 1:
            print(f"PROGRESS {frame_no} {0 if is_image else total_frames}", flush=True)
        fh, fw = frame.shape[:2]
        t0 = time.perf_counter()
        dets = detector.detect(frame)
        if is_image:
            # a single image has no motion for DeepSORT to confirm; use raw detections
            tracks = [(str(i + 1), [d[0][0], d[0][1], d[0][0] + d[0][2], d[0][1] + d[0][3]], d[2], d[1])
                      for i, d in enumerate(dets)]
        else:
            tracks = tracker.track(dets, frame)
        riders = associate(tracks, fw, fh)
        infer_times.append(time.perf_counter() - t0)
        for d in dets:
            counts[d[2]] += 1

        violators = set()
        for rid, r in riders.items():
            if not decide(r):
                why = ("rider cut at image border (head may be outside frame)" if r["edge"] and r["no_helmet"]
                       else "rider wearing helmet" if r["helmet"] is not None
                       else "no bare head detected on rider" if r["no_helmet"] is None
                       else "rider cut at image border")
                skipped[why] = skipped.get(why, 0) + 1
                continue
            violators.add(rid)
            rid_key = alias.get(rid, rid)
            if rid_key not in cases and config.REID_MERGE:
                # DeepSORT ID switch or duplicate track: a new violating track that overlaps
                # a recent case box is treated as the same rider (one case per rider).
                for cid, oc in cases.items():
                    if frame_no - oc["last_frame"] <= config.REID_GAP \
                            and iou(oc["last_box"], r["box"]) > config.REID_IOU:
                        alias[rid] = rid_key = cid
                        oc.setdefault("merged_track_ids", []).append(rid)
                        break
            c = cases.setdefault(rid_key, {"track_id": rid_key, "hits": 0, "first_frame": frame_no,
                                           "best_plate_area": 0, "plate_img": None, "rider_img": None,
                                           "frame_time_ms": None})
            c["hits"] += 1
            c["last_box"], c["last_frame"] = r["box"], frame_no
            rid = rid_key
            x1, y1, x2, y2 = [max(0, v) for v in r["box"]]
            if c["rider_img"] is None or c["hits"] == config.CONFIRM_FRAMES:
                p = os.path.join(out_dir, "riders", f"rider_{rid}_f{frame_no}.png")
                cv2.imwrite(p, frame[y1:y2, x1:x2])
                c["rider_img"] = p
                c["frame_time_ms"] = cap.get(cv2.CAP_PROP_POS_MSEC) if cap else 0
            if r["plate"] is not None:
                px1, py1, px2, py2 = [max(0, v) for v in r["plate"]]
                area = (px2 - px1) * (py2 - py1)
                crops = c.setdefault("plate_crops", [])        # top-K largest crops, for OCR voting
                if area > 0 and (len(crops) < config.PLATE_TOPK or area > crops[-1][0]):
                    p = os.path.join(out_dir, "plates", f"plate_{rid}_f{frame_no}.png")
                    cv2.imwrite(p, frame[py1:py2, px1:px2])
                    crops.append((area, p))
                    crops.sort(key=lambda t: -t[0])
                    for _, old in crops[config.PLATE_TOPK:]:
                        if os.path.exists(old):
                            os.remove(old)
                    del crops[config.PLATE_TOPK:]
                    c.update(best_plate_area=crops[0][0], plate_img=crops[0][1])

        draw(frame, tracks, violators)
        if is_image:
            cv2.imwrite(os.path.join(out_dir, "frames", "annotated_image.png"), frame)
        else:
            writer.write(frame)
            if violators and saved_frames < 6 and frame_no % 10 == 0:
                cv2.imwrite(os.path.join(out_dir, "frames", f"frame_{frame_no}.png"), frame)
                saved_frames += 1

    if cap:
        cap.release()
        writer.release()

    need = 1 if is_image else config.CONFIRM_FRAMES
    confirmed = [c for c in cases.values() if c["hits"] >= need]
    print(f"\nFrames: {frame_no} | candidate tracks: {len(cases)} | confirmed violations: {len(confirmed)}")
    print(f"STAGE ocr {len(confirmed)}", flush=True)

    # ---- post-processing: OCR -> owner lookup -> challan ----
    db = load_db()
    now = dt.datetime.now()
    challans = []
    for i, c in enumerate(confirmed, 1):
        print(f"STAGE case {i} {len(confirmed)}", flush=True)
        rec ={k: v for k, v in c.items() if k not in ("best_plate_area", "last_box", "plate_crops")}
        rec["challan_id"] = f"PTP-{now:%Y%m%d-%H%M%S}-{i:02d}"
        rec["camera_id"], rec["location"] = config.CAMERA_ID, config.LOCATION
        rec["timestamp"] = now.isoformat(timespec="seconds")
        if not c["plate_img"]:
            rec.update(status="manual_review", reason="no plate linked to rider")
        else:
            # OCR every kept crop (largest first) and combine the readings
            t0 = time.perf_counter()
            readings = []
            for _, p in c.get("plate_crops", [(0, c["plate_img"])]):
                pr = read_plate(cv2.imread(p))
                pr["img"] = p
                readings.append(pr)
                if pr["valid"] and lookup_owner(pr["plate"], db):
                    break                               # exact registered plate: no need to read more
            res = resolve_plate(readings, db)
            first = readings[0]
            rec.update(raw_ocr=first["raw_ocr"], ocr_conf=first["ocr_conf"], engine=first["engine"],
                       ocr_readings=[{k: r.get(k) for k in ("raw_ocr", "plate", "valid", "ocr_conf", "img")}
                                     for r in readings],
                       plate=res["plate"] or first["plate"], valid=bool(res["plate"]) or first["valid"],
                       plate_match=res["match"], ocr_seconds=round(time.perf_counter() - t0, 2))
            if res.get("match") == "db_fuzzy":
                rec.update(match_distance=res["match_distance"], matched_from=res["matched_from"])
            owner = res["owner"]
            if not owner:
                any_valid = any(r["valid"] for r in readings)
                rec.update(status="manual_review",
                           reason="plate not found in vehicle database" if any_valid
                           else "plate text failed Indian format check")
            else:
                sms = build_sms(owner, res["plate"], rec["challan_id"], now)
                status, sid = send_sms(owner["phone_number"], sms)
                rec.update(owner=owner["name"], sms_to=owner["phone_number"][:-4] + "XXXX",
                           sms_text=sms, sms_status=status, sms_sid=sid, fine=config.FINE_AMOUNT,
                           status="challan_issued")
                if status == "failed":
                    rec["sms_error"] = notifier.LAST_ERROR
        if rec["status"] == "manual_review" and config.ALERT_ON_REVIEW:
            # every confirmed violation notifies the control room / demo receiver, even before
            # the plate is verified; the e-challan itself is sent after officer approval
            sms = build_alert_sms(rec, now)
            status, sid = send_sms(None, sms)
            rec.update(sms_text=sms, sms_status=status, sms_sid=sid, sms_kind="alert")
            if status == "failed":
                rec["sms_error"] = notifier.LAST_ERROR
        challans.append(rec)
        print(json.dumps(rec, indent=2))

    summary = {
        "source": os.path.basename(source), "frames": frame_no,
        "resolution": f"{fw}x{fh}", "video_fps": fps,
        "avg_detect_track_ms": round(1000 * sum(infer_times) / max(1, len(infer_times)), 1),
        "processing_fps": round(len(infer_times) / max(1e-9, sum(infer_times)), 1),
        "detections_per_class": counts, "candidate_tracks": len(cases),
        "confirmed_violations": len(confirmed),
        "challans_issued": sum(1 for c in challans if c.get("status") == "challan_issued"),
        "manual_review": sum(1 for c in challans if c.get("status") == "manual_review"),
        "skipped_riders": skipped,
    }
    with open(os.path.join(out_dir, "challans.json"), "w", encoding="utf-8") as f:
        json.dump(challans, f, indent=2)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("\nSUMMARY", json.dumps(summary, indent=2))
    print("STAGE done", flush=True)
    return summary, challans


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    name = os.path.splitext(os.path.basename(a.source))[0]
    run(a.source, a.out or os.path.join(config.OUTPUT_DIR, name))
    # All results are on disk now. Exit immediately so a crash while CUDA / EasyOCR / gRPC threads
    # tear down at interpreter shutdown cannot turn a successful run into a non-zero exit code.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
