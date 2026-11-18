"""Print architecture and training arguments stored in the trained weights."""
import os, sys, json
import torch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ultralytics import YOLO
import config

ck = torch.load(config.MODEL_PATH, map_location="cpu", weights_only=False)
print("keys:", list(ck.keys()))
print("date:", ck.get("date"), "version:", ck.get("version"), "epoch:", ck.get("epoch"))
ta = ck.get("train_args", {})
print(json.dumps({k: ta.get(k) for k in ("model", "data", "epochs", "imgsz", "batch", "optimizer", "lr0", "patience",
                                          "device", "mosaic", "hsv_h", "fliplr", "close_mosaic", "amp")}, indent=1))
m = YOLO(config.MODEL_PATH)
print("names:", m.names)
print("yaml:", m.model.yaml.get("yaml_file"), "scale:", m.model.yaml.get("scale"))
m.info(verbose=False)
