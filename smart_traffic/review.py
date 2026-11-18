"""Officer review of cases in the manual-review queue (human-in-the-loop, SRS REQ-24 / REQ-33).

Usage:
    python review.py --run outputs/test5_image                       # list cases
    python review.py --run outputs/test5_image --case PTP-... --plate MH21BW0214 --approve
    python review.py --run outputs/test5_image --case PTP-... --reject
"""
import argparse
import datetime as dt
import json
import os

import config
import notifier
from notifier import build_sms, load_db, lookup_owner, send_sms
from plate_reader import is_valid, normalise


def refresh_summary(run_dir, cases):
    """Keep summary.json counts in sync with challans.json after an officer action."""
    sp = os.path.join(run_dir, "summary.json")
    if not os.path.exists(sp):
        return
    with open(sp, encoding="utf-8") as f:
        s = json.load(f)
    s["challans_issued"] = sum(1 for c in cases if c.get("status") == "challan_issued")
    s["manual_review"] = sum(1 for c in cases if c.get("status") == "manual_review")
    s["rejected"] = sum(1 for c in cases if c.get("status") == "rejected")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def apply_review(run_dir, case_id, approve, plate=None, officer="Officer-01"):
    """Approve (issue e-challan + SMS) or reject one case. Returns (ok, message)."""
    path = os.path.join(run_dir, "challans.json")
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)
    c = next((x for x in cases if x["challan_id"] == case_id), None)
    if c is None:
        return False, f"Case {case_id} not found."

    if approve:
        p = normalise(plate or (c.get("plate") if c.get("valid") else ""))
        if not p:
            return False, (f"Case {case_id}: type the number plate (read it from the evidence photo) "
                           f"before approving.")
        if not is_valid(p):
            return False, f"Case {case_id}: '{p}' is not a valid Indian registration number (e.g. MH12AB1234)."
        now = dt.datetime.now()
        owner = lookup_owner(p, load_db())
        name = owner["name"] if owner else "Vehicle Owner"
        sms = build_sms({"name": name}, p, case_id, now)
        status, sid = send_sms(owner["phone_number"] if owner else None, sms)
        c.update(plate=p, valid=True, plate_source="officer_corrected" if plate else "ocr",
                 owner=owner["name"] if owner else "Not in demo vehicle DB",
                 sms_to=(owner["phone_number"][:-4] + "XXXX") if owner else "demo receiver",
                 sms_text=sms, sms_status=status, sms_sid=sid, sms_kind="challan",
                 sms_error=notifier.LAST_ERROR if status == "failed" else None,
                 fine=config.FINE_AMOUNT, status="challan_issued", reason=None)
        sms_note = {"failed": f"SMS failed: {notifier.LAST_ERROR}", "dry-run": "SMS dry-run (not sent)."}.get(
            status, f"SMS sent to demo receiver ({status}).")
        msg = f"APPROVED {case_id}: e-challan issued for {p} (Rs. {config.FINE_AMOUNT}). {sms_note}"
    else:
        c["status"] = "rejected"
        msg = f"REJECTED {case_id}: no challan, no SMS."
    c.update(reviewed_by=officer, reviewed_at=dt.datetime.now().isoformat(timespec="seconds"),
             review_action="approved" if approve else "rejected")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cases, f, indent=2)
    refresh_summary(run_dir, cases)
    return True, msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--case")
    ap.add_argument("--plate")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--approve", action="store_true")
    g.add_argument("--reject", action="store_true")
    ap.add_argument("--officer", default="Officer-01")
    a = ap.parse_args()

    if not a.case:
        with open(os.path.join(a.run, "challans.json"), encoding="utf-8") as f:
            cases = json.load(f)
        print(f"{'Challan ID':24s} {'Track':6s} {'OCR text':16s} {'Status':14s} Reason")
        for c in cases:
            print(f"{c['challan_id']:24s} {str(c['track_id']):6s} {c.get('raw_ocr', '') or '-':16s} "
                  f"{c['status']:14s} {c.get('reason', '') or ''}")
            print(f"{'':24s} evidence: {c.get('rider_img')} | {c.get('plate_img')}")
        return
    ok, msg = apply_review(a.run, a.case, not a.reject, a.plate, a.officer)
    print(msg)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
