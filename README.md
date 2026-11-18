# Smart Traffic Safety & Automated e-Challan System

Detects motorcycle riders **without a helmet** in traffic video, reads the **number plate**, finds the
**vehicle owner** and issues an **e-challan by SMS**. Uncertain cases go to an **officer review** dashboard.

`YOLO11s` (rider / helmet / no_helmet / number_plate) → `DeepSORT` tracking → IoU association + edge filter →
3-frame confirmation → `EasyOCR` + rule correction (+ `Mistral 7B` via Ollama) → database matching →
`Twilio` SMS. Web dashboard at http://localhost:8000.

| Result | Value |
|---|---|
| Detector (validation) | Precision 0.906 · Recall 0.892 · mAP@0.5 0.942 |
| Speed (RTX 3050 laptop GPU) | ~33 fps, ~30 ms per frame |
| Unit tests | 14 / 14 pass |

Group BE-A38 · Vishwakarma Institute of Information Technology, Pune · Guide: Dr. Amruta Patil

---

## Setup on a brand-new Windows PC (nothing installed)

Follow the steps in order. Total time: about 20–40 minutes, mostly downloads.
You need: Windows 10/11 (64-bit), internet, ~10 GB free disk.

### Step 1 – Install Python 3.11

The project needs **Python 3.11** (3.12 also works). Newer versions such as 3.13, 3.14 or 3.15 are **not**
supported by PyTorch 2.5.1. If a newer Python is already installed, **keep it**: install 3.11 next to it, and
`setup.ps1` picks 3.11 automatically.

**Option A – a newer Python is already installed (has the `py` command):** in PowerShell run
```
py install 3.11
```
(If that says *unknown command*, use Option B instead.)

**Option B – installer:**
1. Open https://www.python.org/downloads/release/python-3119/ and scroll to **Files**.
2. Download **Windows installer (64-bit)** and run it.
3. On the first screen tick **"Add python.exe to PATH"** (and keep *"Use admin privileges when installing py.exe"*), then click **Install Now**.

**Check it:** open a **new** PowerShell window (Start menu → type `PowerShell`) and run:
```
py -3.11 --version
```
It must print `Python 3.11.x`. (`python --version` may still show 3.15, which is fine.)

### Step 2 – Install Git

1. Download from https://git-scm.com/download/win and run the installer.
2. Keep every option at its default, click **Next** until **Install**.
3. Close and reopen PowerShell, then check: `git --version`

*(No Git? On the GitHub page click **Code → Download ZIP**, extract it, and skip Step 4's `git clone` line.)*

### Step 3 – (Optional) GPU driver and Ollama

* **NVIDIA GPU:** install the latest driver from https://www.nvidia.com/Download/index.aspx.
  No GPU? Skip this. Everything runs on the CPU, just slower.
* **Ollama** (local Mistral LLM for plate correction and SMS wording):
  download from https://ollama.com/download/windows and install.
  Skip it if you like. The system then uses rule-based correction and a template SMS.

### Step 4 – Download the project

In PowerShell:
```
cd $HOME\Desktop
git clone https://github.com/omgirnare/Smart-E-Challan-System.git
cd Smart-E-Challan-System
```

### Step 5 – Run the setup script (one time)

Still in the `Smart-E-Challan-System` folder:
```
powershell -ExecutionPolicy Bypass -File setup.ps1
```
It does everything automatically:
1. Finds Python 3.11.
2. Creates a private environment in `.venv`.
3. Installs PyTorch: the GPU build if an NVIDIA GPU is found, otherwise the CPU build.
4. Installs all packages at the exact tested versions (`smart_traffic\requirements.txt`).
5. Creates `smart_traffic\.env` and downloads the EasyOCR models. If Ollama is installed, it also downloads Mistral (4.4 GB).
6. Runs the 14 unit tests. You should see **`14 passed`** and **`Setup complete`**.

Options: `-Cpu` forces CPU mode, and `-SkipOllama` skips the 4.4 GB Mistral download.

### Step 6 – Start the system

Double-click **`run_dashboard.bat`** in the project folder. The first start takes about a minute.
The browser opens **http://localhost:8000** by itself. Keep the black window open, because closing it stops the system.

In the dashboard:
1. Pick a sample (for example `test2.mp4`) and click **Process sample**, or upload your own video or photo.
2. Leave **"Send SMS"** unchecked for a dry run. The SMS text is shown but not sent.
3. Watch the progress bar, then view the annotated video, the violations and the challans.
4. Cases the system could not finish appear under **Officer review**: type the plate, then **Approve & send** or **Reject**.

---

## Real SMS (optional)

Edit `smart_traffic\.env` with Notepad and fill in your Twilio values:
```
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE=+1...           # your Twilio number
DEMO_RECEIVER=+91...         # trial accounts can only text VERIFIED numbers
```
Never upload `.env` to GitHub: it is listed in `.gitignore`.

## Command line (without the dashboard)

```
cd smart_traffic
..\.venv\Scripts\python main.py --source assets\test3.mp4        # results in outputs\test3\
..\.venv\Scripts\python review.py --run outputs\test3             # list cases needing review
..\.venv\Scripts\python -m pytest tests -v                        # unit tests
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `running scripts is disabled on this system` | Use the exact command from Step 5 (it contains `-ExecutionPolicy Bypass`) |
| `Python 3.11 not found` | Install Python 3.11 (Step 1; it can sit next to 3.13/3.14/3.15), then open a **new** PowerShell and check `py -3.11 --version` |
| `.venv` was created with the wrong Python (e.g. 3.15) | Delete the `.venv` folder, install 3.11, run `setup.ps1` again |
| `python` opens the Microsoft Store | Settings → Apps → Advanced app settings → App execution aliases → turn off both *python* entries |
| `.venv not found` when starting | Run Step 5 first |
| GPU present but setup says *CPU mode* | Update the NVIDIA driver, delete the `.venv` folder, run Step 5 again |
| Port 8000 already in use | In PowerShell: `$env:DASHBOARD_PORT=8080; .\run_dashboard.bat`, then open http://localhost:8080 |
| `[Mistral] unavailable` in the log | Ollama is not running. Start it with `ollama serve`, or ignore it (the fallback is used) |
| Very slow processing | Normal without an NVIDIA GPU: roughly 5–10× slower than the GPU figures above |

## Linux (Ubuntu) quick setup

```
sudo apt install -y python3.11 python3.11-venv git
git clone https://github.com/omgirnare/Smart-E-Challan-System.git && cd Smart-E-Challan-System
python3.11 -m venv .venv
.venv/bin/pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu121   # or .../whl/cpu
.venv/bin/pip install -r smart_traffic/requirements.txt
cp smart_traffic/.env.example smart_traffic/.env
cd smart_traffic && ../.venv/bin/python dashboard.py
```

## Project layout

```
setup.ps1               one-time installer (Windows)
run_dashboard.bat       starts Ollama + dashboard + browser
smart_traffic/
  main.py               pipeline: detect → track → decide → OCR → challan
  detector.py           YOLO11s + DeepSORT wrappers
  association.py        IoU binding, edge rule, violation decision
  plate_reader.py       EasyOCR variants, rule correction, Mistral fallback
  notifier.py           owner lookup, fuzzy plate matching, Twilio SMS
  review.py             officer approve / reject
  dashboard.py          web UI (http://localhost:8000)
  config.py             thresholds; secrets come from .env
  models/best.pt        trained YOLO11s weights
  database/vehicles.json  demo vehicle-owner records (fictional)
  assets/               sample videos and images
  outputs/              results of earlier runs (shown in the dashboard)
  tests/                pytest unit tests
```
