"""Owner lookup and e-challan SMS through Twilio (dry-run when credentials are absent)."""
import json
import os
import datetime as dt
import config
from plate_reader import normalise, mistral_challan_text


def load_db():
    with open(config.DB_PATH, encoding="utf-8") as f:
        return json.load(f)


def lookup_owner(plate, db=None):
    db = db or load_db()
    p = normalise(plate)
    return next((r for r in db if normalise(r["number_plate_number"]) == p), None)


# Character pairs EasyOCR commonly confuses on Indian plates; substituting one for the other
# costs half an edit in the distance below.
CONFUSABLE = [set(s) for s in ("0ODQ", "1IL", "2Z", "5S", "6G", "8B", "UWV", "4A", "7T", "MN", "HM", "CG")]


def _sub_cost(a, b):
    if a == b:
        return 0.0
    return 0.5 if any(a in s and b in s for s in CONFUSABLE) else 1.0


def plate_distance(a, b):
    """Levenshtein distance where OCR-confusable substitutions cost 0.5."""
    a, b = normalise(a), normalise(b)
    prev = [float(j) for j in range(len(b) + 1)]
    for i in range(1, len(a) + 1):
        cur = [float(i)] + [0.0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + _sub_cost(a[i - 1], b[j - 1]))
        prev = cur
    return prev[-1]


def fuzzy_lookup(candidates, db=None):
    """Database-assisted plate correction (as used in ANPR hot-list matching).

    Compares every OCR candidate string with every registered plate. Accepts the best
    match only if it is within FUZZY_MAX_DIST and clearly better than the runner-up,
    so an ambiguous reading is never forced onto a vehicle. Returns (owner, dist, cand) or None.
    """
    db = db or load_db()
    scored = []
    for r in db:
        best = min(((plate_distance(c, r["number_plate_number"]), c) for c in candidates if c),
                   default=(99.0, ""))
        scored.append((best[0], best[1], r))
    scored.sort(key=lambda t: t[0])
    if not scored:
        return None
    dist, cand, rec = scored[0]
    # The 4-digit registration number is the largest, most legible part of the plate:
    # when OCR reads it exactly, a slightly larger error in the small state/series text is tolerated.
    last4_ok = normalise(cand)[-4:] == normalise(rec["number_plate_number"])[-4:]
    limit = config.FUZZY_MAX_DIST_LAST4 if last4_ok else config.FUZZY_MAX_DIST
    if dist > limit:
        return None
    if len(scored) > 1 and scored[1][0] - scored[0][0] < config.FUZZY_MARGIN:
        return None
    return scored[0][2], scored[0][0], scored[0][1]


def resolve_plate(readings, db=None):
    """Combine OCR readings from several frames of the same rider.

    1. exact: a valid plate that is registered (majority vote across frames)
    2. fuzzy: DB-assisted match of all readings (see fuzzy_lookup)
    Returns dict with plate, owner, match ('exact' | 'db_fuzzy' | None) and votes.
    """
    db = db or load_db()
    exact = {}
    for pr in readings:
        if pr.get("valid") and lookup_owner(pr["plate"], db):
            exact[pr["plate"]] = exact.get(pr["plate"], 0) + 1
    if exact:
        plate = max(exact, key=exact.get)
        return {"plate": plate, "owner": lookup_owner(plate, db), "match": "exact",
                "votes": exact[plate], "readings": len(readings)}
    cands = set()
    for pr in readings:
        cands.update(x for x in (pr.get("plate"), pr.get("llm_suggestion"), normalise(pr.get("raw_ocr"))) if x)
    hit = fuzzy_lookup(sorted(cands), db)
    if hit:
        owner, dist, cand = hit
        return {"plate": owner["number_plate_number"], "owner": owner, "match": "db_fuzzy",
                "match_distance": dist, "matched_from": cand, "readings": len(readings)}
    return {"plate": None, "owner": None, "match": None, "readings": len(readings)}


def build_sms(owner, plate, challan_id, when):
    due = (when + dt.timedelta(days=15)).strftime("%d-%m-%Y")
    kw = dict(name=owner["name"], plate=plate, date=when.strftime("%d-%m-%Y"),
              time=when.strftime("%H:%M"), location=config.LOCATION, cid=challan_id,
              fine=config.FINE_AMOUNT, due=due)
    text = mistral_challan_text(**kw)
    if not text:
        text = (f"Pune Traffic Police e-Challan {challan_id}: Dear {owner['name']}, vehicle {plate} "
                f"was recorded riding without helmet on {kw['date']} {kw['time']} at {config.LOCATION}. "
                f"Fine Rs.{config.FINE_AMOUNT} under MV Act 194D. Pay by {due}. "
                f"To contest, quote {challan_id}.")
    return text


def build_alert_sms(rec, when):
    """Violation alert for a case that still needs officer verification (no owner yet)."""
    plate = rec.get("plate") if rec.get("valid") else None
    return (f"Traffic alert {rec['challan_id']}: no-helmet violation detected by {config.CAMERA_ID} at "
            f"{config.LOCATION} on {when:%d-%m-%Y %H:%M}. Plate: {plate or 'not readable'}. "
            f"Evidence saved; pending officer verification before e-challan.")


LAST_ERROR = None


def _friendly_twilio_error(e):
    s = str(e)
    if "21608" in s or "verified" in s.lower() or "No Twilio trial phone number" in s:
        return ("Twilio trial: the receiver number is not verified / no Twilio number is assigned. "
                "In the Twilio console verify DEMO_RECEIVER and buy a trial number, then put it in TWILIO_PHONE.")
    if "21408" in s or "permission" in s.lower():
        return "Twilio: SMS to this country is disabled. Enable India under Messaging > Geo permissions."
    if "20003" in s or "authenticate" in s.lower():
        return "Twilio: wrong Account SID / Auth Token in .env."
    if "21606" in s or "21659" in s or "21212" in s:
        return "Twilio: TWILIO_PHONE is not a number owned by this Twilio account."
    return s[:300]


def send_sms(to_number, body):
    """Send via Twilio. Returns (status, sid). On failure the reason is in LAST_ERROR."""
    global LAST_ERROR
    LAST_ERROR = None
    if os.getenv("SMS_DRY_RUN") == "1" or not (
            config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_PHONE):
        print("[Twilio] DRY-RUN (message not sent)")
        return "dry-run", None
    from twilio.rest import Client
    # Twilio trial accounts can only text verified numbers; DEMO_RECEIVER redirects demo SMS.
    to_number = config.DEMO_RECEIVER or to_number
    if not to_number:
        LAST_ERROR = "No receiver: set DEMO_RECEIVER in .env"
        return "failed", None
    try:
        msg = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN).messages.create(
            body=body, from_=config.TWILIO_PHONE, to=to_number)
        print(f"[Twilio] sent to ...{to_number[-4:]} sid={msg.sid} status={msg.status}")
        return msg.status, msg.sid
    except Exception as e:
        LAST_ERROR = _friendly_twilio_error(e)
        print(f"[Twilio] send failed: {e}")
        return "failed", None
