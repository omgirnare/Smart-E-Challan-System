"""Central configuration for the Smart Traffic Safety & Automated Challan System."""
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

MODEL_PATH = os.getenv("MODEL_PATH", os.path.join(BASE_DIR, "models", "best.pt"))
CLASSES = ["helmet", "no_helmet", "number_plate", "rider"]

CONF_THRESHOLD = float(os.getenv("CONF_THRESHOLD", 0.50))   # detection confidence
IOU_LINK_THRESHOLD = 0.01                                   # min IoU to bind object -> rider
EDGE_MARGIN = 5                                             # px; riders touching border ignored
CONFIRM_FRAMES = int(os.getenv("CONFIRM_FRAMES", 3))        # no_helmet frames before a case is confirmed
EDGE_RULE = os.getenv("EDGE_RULE", "lrt")                   # "all" = v1 (4 borders), "lrt" = v2 (left/right/top)
REID_MERGE = os.getenv("REID_MERGE", "1") == "1"            # merge cases across DeepSORT ID switches
REID_IOU, REID_GAP = 0.5, 30                               # overlap and frame gap for the merge
TRACK_MAX_AGE = 20                                          # DeepSORT max_age
PLATE_TOPK = int(os.getenv("PLATE_TOPK", 5))                # plate crops per rider used for OCR voting
FUZZY_MAX_DIST = float(os.getenv("FUZZY_MAX_DIST", 2.0))    # max confusion-weighted edit distance to a DB plate
FUZZY_MAX_DIST_LAST4 = 3.5                                  # ...allowed when the last 4 digits match exactly
FUZZY_MARGIN = 1.5                                         # best DB match must beat the runner-up by this

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

FINE_AMOUNT = int(os.getenv("FINE_AMOUNT", 1000))           # Rs., MV Act Sec. 194D
CAMERA_ID = os.getenv("CAMERA_ID", "CAM-01")
LOCATION = os.getenv("LOCATION", "Kondhwa Junction, Pune")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
DEMO_RECEIVER = os.getenv("DEMO_RECEIVER")                  # all demo SMS go here (Twilio trial)
ALERT_ON_REVIEW = os.getenv("ALERT_ON_REVIEW", "1") == "1"  # SMS alert for violations awaiting review

DB_PATH = os.path.join(BASE_DIR, "database", "vehicles.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
