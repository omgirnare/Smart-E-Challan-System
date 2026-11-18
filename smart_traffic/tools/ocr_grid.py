"""Small parameter grid for EasyOCR on plate crops."""
import os, sys, itertools
import cv2
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from plate_reader import _get_reader, rule_correct, is_valid

AL = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
r = _get_reader()
for p in sys.argv[1:]:
    img = cv2.imread(p)
    print("==", p)
    for scale, pad, dec, mag in itertools.product((1, 2, 3), (0, 15), ("greedy", "beamsearch"), (1.0, 2.0)):
        v = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        g = cv2.cvtColor(v, cv2.COLOR_BGR2GRAY)
        if pad:
            g = cv2.copyMakeBorder(g, pad * scale, pad * scale, pad * scale, pad * scale, cv2.BORDER_REPLICATE)
        res = r.readtext(g, allowlist=AL, decoder=dec, mag_ratio=mag, width_ths=1.5, text_threshold=0.5,
                         low_text=0.3, link_threshold=0.3)
        res.sort(key=lambda q: (q[0][0][1] // (20 * scale), q[0][0][0]))
        txt = "".join(q[1] for q in res)
        conf = sum(q[2] for q in res) / len(res) if res else 0
        rc = rule_correct(txt)
        flag = "  <== VALID" if is_valid(rc) else ""
        print(f"  s={scale} pad={pad:2d} {dec:10s} mag={mag} raw={txt:14s} conf={conf:.2f} -> {rc}{flag}")
