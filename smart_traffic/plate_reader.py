"""Number-plate reading: EasyOCR for characters, Mistral (Ollama) for correction."""
import json
import re
import cv2
import easyocr
import config

# Indian formats: standard (MH12AB1234, DL3CAB1234) and Bharat series (22BH1234AA)
PLATE_RE = re.compile(r"^([A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}|\d{2}BH\d{4}[A-Z]{1,2})$")
STATE_CODES = {"AN", "AP", "AR", "AS", "BR", "CH", "CG", "DD", "DL", "GA", "GJ", "HR", "HP",
               "JK", "JH", "KA", "KL", "LA", "LD", "MP", "MH", "MN", "ML", "MZ", "NL", "OD",
               "PY", "PB", "RJ", "SK", "TN", "TS", "TR", "UP", "UK", "WB"}

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en"], gpu=True, verbose=False)
    return _reader


def normalise(text):
    return re.sub(r"[^A-Za-z0-9]", "", text or "").upper()


def is_valid(plate):
    return bool(PLATE_RE.match(plate)) and (plate[:2] in STATE_CODES or plate[2:4] == "BH")


def preprocess(img):
    """Upscale x2, grayscale, CLAHE contrast and light denoise."""
    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return cv2.bilateralFilter(gray, 7, 50, 50)


AL = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
# (upscale factor, allowlist, CLAHE) variants found best in tools/ocr_grid.py experiments
OCR_VARIANTS = [(4, None, False), (3, AL, False), (2, AL, True)]


def _read_variant(img, scale, allow, clahe):
    v = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if clahe:
        v = preprocess(img)
    res = _get_reader().readtext(v, allowlist=allow)
    if not res:
        return "", 0.0
    res.sort(key=lambda r: (r[0][0][1] // (20 * scale), r[0][0][0]))   # top-to-bottom, left-to-right
    return " ".join(r[1] for r in res), sum(r[2] for r in res) / len(res)


def easyocr_read(img):
    """Run several pre-processing variants; prefer one whose rule-corrected text is a valid
    Indian plate, otherwise the reading with the most alphanumeric characters."""
    best = ("", 0.0)
    for scale, allow, clahe in OCR_VARIANTS:
        text, conf = _read_variant(img, scale, allow, clahe)
        if is_valid(rule_correct(text)):
            return text, conf
        if len(normalise(text)) > len(normalise(best[0])):
            best = (text, conf)
    return best


MISTRAL_PROMPT = """You correct OCR output of Indian vehicle number plates.
Format: 2 letters (state code) + 1-2 digits (district) + 0-3 letters (series) + exactly 4 digits.
Bharat series: 2 digits + BH + 4 digits + 1-2 letters.
In letter positions replace 0->O, 1->I, 5->S, 8->B, 2->Z, 6->G. In digit positions replace O->0, I->1, S->5, B->8, Z->2, G->6, D->0.
Drop characters that are clearly noise (e.g. IND, dots, extra symbols).
Examples:
OCR "MH 12 AB I234" -> {{"plate": "MH12AB1234"}}
OCR "IND KA05 MN 67B9" -> {{"plate": "KA05MN6789"}}
OCR "M H14 GH 77BB" -> {{"plate": "MH14GH7788"}}
OCR "{raw}" -> ?
Return ONLY JSON: {{"plate": "<corrected plate>", "reason": "<short>"}}"""


def mistral_correct(raw):
    """Ask local Mistral (via Ollama) to correct OCR confusions. Returns dict or None."""
    try:
        import ollama
        client = ollama.Client(host=config.OLLAMA_HOST)
        r = client.generate(model=config.OLLAMA_MODEL, prompt=MISTRAL_PROMPT.format(raw=raw),
                            format="json", options={"temperature": 0})
        data = json.loads(r["response"])
        data["plate"] = normalise(data.get("plate", ""))
        return data
    except Exception as e:  # Ollama not running / bad JSON -> fall back to rule-based
        print(f"[Mistral] unavailable: {e}")
        return None


TO_L = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G", "4": "A", "7": "T"}
TO_D = {"O": "0", "I": "1", "S": "5", "B": "8", "Z": "2", "G": "6", "D": "0", "Q": "0", "L": "1", "T": "7", "A": "4"}


def rule_correct(raw):
    """Deterministic position-aware correction of letter/digit confusions.

    Strips a leading 'IND' (hologram text), then forces letters in the state-code
    positions and digits in the district and last-4 positions.
    """
    p = normalise(raw)
    if p.startswith("IND") and len(p) > 10:
        p = p[3:]
    if len(p) < 8:
        return p
    if p[2:4] in ("BH", "8H") and len(p) >= 9:            # Bharat series: NN BH NNNN L(L)
        head = "".join(TO_D.get(c, c) for c in p[:2])
        mid = "".join(TO_D.get(c, c) for c in p[4:8])
        tail = "".join(TO_L.get(c, c) for c in p[8:])
        return head + "BH" + mid + tail
    state = "".join(TO_L.get(c, c) for c in p[:2])
    last4 = "".join(TO_D.get(c, c) for c in p[-4:])
    middle = p[2:-4]
    district = "".join(TO_D.get(c, c) for c in middle[:2])
    if len(middle) > 2 and not middle[1].isdigit() and middle[1] not in TO_D:
        district, series = TO_D.get(middle[0], middle[0]), middle[1:]
    else:
        series = middle[2:]
    series = "".join(TO_L.get(c, c) for c in series)
    return state + district + series + last4


def read_plate(img):
    """Full plate-reading step: EasyOCR -> rule correction -> Mistral (only if still invalid)."""
    raw, conf = easyocr_read(img)
    out = {"raw_ocr": raw, "ocr_conf": round(conf, 3), "plate": "", "valid": False, "engine": "easyocr"}
    if not raw:
        return out
    ruled = rule_correct(raw)
    if is_valid(ruled):
        out.update(plate=ruled, valid=True, engine="easyocr+rules")
        return out
    m = mistral_correct(raw)
    if m and is_valid(m["plate"]):
        out.update(plate=m["plate"], valid=True, engine="easyocr+mistral", llm_reason=m.get("reason", ""))
    else:
        out.update(plate=ruled, valid=False, engine="easyocr+rules+mistral",
                   llm_suggestion=(m or {}).get("plate", ""))
    return out


CHALLAN_PROMPT = """Write a short, polite e-challan SMS (max 300 characters, plain text, no emojis) from
Pune Traffic Police to {name} for vehicle {plate}: riding without helmet (MV Act Sec. 129/194D)
on {date} at {time}, {location}. Challan ID {cid}. Fine Rs. {fine}, pay by {due}.
Mention they can contest it with the evidence ID {cid}. Return only the SMS text."""


def mistral_challan_text(**kw):
    try:
        import ollama
        client = ollama.Client(host=config.OLLAMA_HOST)
        r = client.generate(model=config.OLLAMA_MODEL, prompt=CHALLAN_PROMPT.format(**kw),
                            options={"temperature": 0.2})
        return r["response"].strip().strip('"')
    except Exception as e:
        print(f"[Mistral] challan text fallback: {e}")
        return None
