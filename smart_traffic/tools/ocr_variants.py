"""Compare EasyOCR results on plate crops under different pre-processing variants."""
import os, sys, glob
import cv2
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from plate_reader import _get_reader, rule_correct, is_valid

AL = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def variants(img):
    up = cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    g = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    yield "color_x3", up
    yield "gray_x3", g
    yield "clahe_x3", cv2.createCLAHE(2.0, (8, 8)).apply(g)
    yield "otsu_x3", cv2.threshold(cv2.GaussianBlur(g, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    yield "sharp_x3", cv2.addWeighted(g, 1.8, cv2.GaussianBlur(g, (0, 0), 3), -0.8, 0)


for p in sys.argv[1:]:
    img = cv2.imread(p)
    print("==", p, img.shape)
    for name, v in variants(img):
        res = _get_reader().readtext(v, allowlist=AL, paragraph=False)
        res.sort(key=lambda r: (r[0][0][1] // 25, r[0][0][0]))
        txt = "".join(r[1] for r in res)
        conf = sum(r[2] for r in res) / len(res) if res else 0
        rc = rule_correct(txt)
        print(f"  {name:10s} raw={txt:16s} conf={conf:.2f} -> {rc:12s} valid={is_valid(rc)}")
        pad = cv2.copyMakeBorder(v, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=(255, 255, 255) if v.ndim == 3 else 255)
        g = pad if pad.ndim == 2 else cv2.cvtColor(pad, cv2.COLOR_BGR2GRAY)
        res = _get_reader().recognize(g, allowlist=AL, detail=1)
        txt = "".join(r[1] for r in res); conf = res[0][2] if res else 0
        rc = rule_correct(txt)
        print(f"  {name+'+rec':14s} raw={txt:16s} conf={conf:.2f} -> {rc:12s} valid={is_valid(rc)}")
