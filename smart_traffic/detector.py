"""YOLOv11 detector + DeepSORT tracker wrappers."""
from ultralytics import YOLO
from deep_sort_realtime.deepsort_tracker import DeepSort
import config


class Detector:
    def __init__(self, model_path=config.MODEL_PATH, conf=config.CONF_THRESHOLD):
        self.model = YOLO(model_path)
        self.conf = conf

    def detect(self, frame):
        """Return detections as ([x, y, w, h], conf, label) for DeepSORT."""
        result = self.model(frame, conf=self.conf, verbose=False)[0]
        dets = []
        for box in result.boxes:
            label = result.names[int(box.cls[0])]
            if label not in config.CLASSES:
                continue
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            dets.append(([x1, y1, x2 - x1, y2 - y1], float(box.conf[0]), label))
        return dets


class Tracker:
    def __init__(self):
        self.ds = DeepSort(max_age=config.TRACK_MAX_AGE)

    def track(self, detections, frame):
        """Return list of (track_id, [x1,y1,x2,y2], label, conf) for confirmed tracks."""
        out = []
        for t in self.ds.update_tracks(detections, frame=frame):
            if not t.is_confirmed():
                continue
            out.append((t.track_id, [int(v) for v in t.to_ltrb()], t.det_class, t.det_conf))
        return out
