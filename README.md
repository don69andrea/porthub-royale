# PortHub Royale — Local Turnaround Prototype (MAKEathon)

This repo is a **local** prototype of the AWS pipeline you presented:
- **YOLOv8** for object detection
- **ByteTrack** for tracking
- (optional) **Pose** (lightweight: MediaPipe) for action hints
- a **rule engine** that checks a **symbolic process model** (RDF/Turtle) and raises alerts
- a small **UI** (Streamlit) to run the analysis on an MP4 and inspect alerts

The original MAKEathon slide stack describes the intended workflow: detection → tracking → analysis → rule engine → alerts  
(see “Technical Architecture…” and “Workflow” slides in your deck).

---

## 1) Setup (VS Code)

### Create venv
```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate  # Windows
```

### Install deps
```bash
pip install -r requirements.txt
```

If `torch/ultralytics` is heavy on your machine, you can still run **Demo Mode** in the UI.

---

## 2) Run the UI

Put your video here:
```
data/input.mp4
```

Then:
```bash
streamlit run app.py
```

---

## 3) What you get

- Frame-by-frame overlays (boxes + track IDs)
- A live **alert list** (rule violations)
- A lightweight **process status** derived from `assets/Swissport_3.ttl`

---

## 4) Files

- `app.py` Streamlit UI
- `src/infer.py` video sampling + YOLO + tracking + result export
- `src/rules.py` rules (proximity, ordering, dwell-time)
- `src/kg.py` RDF loading + mapping between detections and process steps
- `config/rules.yaml` tweak thresholds + mappings

---

## Notes on the RDF/Turtle model

`assets/Swissport_3.ttl` is a BPMN-like process graph with labeled steps such as:
- “Place rear chock”, “Place front chock”
- “Pull GPU cable to aircraft”, “Insert cable and latch”
- “SLA breach / escalation”

The prototype uses these labels as **symbolic anchors** (hybrid AI angle): your detections/events can be mapped to step completion.
