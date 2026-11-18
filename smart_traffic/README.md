# Smart Traffic Safety & Automated Challan System (Review 2 build)

Pipeline: YOLO11s (rider / helmet / no_helmet / number_plate) → DeepSORT → IoU association + edge filter →
N-frame confirmation + track merge → evidence → EasyOCR + rule correction (+ Mistral via Ollama) →
owner lookup → Twilio e-challan SMS. Uncertain cases go to officer review (`review.py`).

## Setup (already done on this laptop)
```
python -m venv ..\.venv
..\.venv\Scripts\pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu121
..\.venv\Scripts\pip install ultralytics==8.3.40 deep-sort-realtime==1.3.2 easyocr==1.7.2 twilio python-dotenv ollama pytest
ollama pull mistral
copy .env.example .env      # then fill in Twilio values for real SMS
```

## Dashboard (demo)
```
ollama serve                                   # if Ollama is not already running
..\.venv\Scripts\python dashboard.py           # open http://localhost:8000
```
Upload a video/image (or pick a sample) -> live progress -> annotated video, violations, challans.
"Send real SMS" unchecked = dry run. Manual-review cases can be approved/rejected from the table.

## Run (command line)
```
..\.venv\Scripts\python main.py --source assets\test3.mp4          # outputs\test3\...
..\.venv\Scripts\python review.py --run outputs\test5_image        # list cases
..\.venv\Scripts\python review.py --run outputs\test5_image --case <ID> --plate MH21BW0214 --approve
..\.venv\Scripts\python -m pytest tests -v
```
Without Twilio credentials the SMS step runs in DRY-RUN mode (message printed, not sent).
Twilio trial accounts can only text verified numbers: set `DEMO_RECEIVER=+91...` in `.env`.

## Outputs per run (`outputs/<name>/`)
`output_annotated.mp4`, `frames/`, `riders/`, `plates/`, `challans.json`, `summary.json`, `console.log`.
`outputs_v1/` holds the results with the original (v1) rules for comparison.
