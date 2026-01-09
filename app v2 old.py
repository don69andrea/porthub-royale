# app.py
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
import streamlit as st
from PIL import Image

from src.infer import (
    list_frames,
    parse_roi,
    SimpleIoUTracker,
    load_model,
    yolo_detect,
    demo_detections,
    detections_df_from_tracked,
    draw_overlay,
)

from src.rules_engine import TASKS, eval_tasks, compute_alerts_df, AlertItem
from src.turnaround_sequence import SequenceState, update_sequence


# ----------------------------
# Page / theme
# ----------------------------
st.set_page_config(page_title="PortHub Royale — Dispatcher Desk", layout="wide")

st.markdown(
    """
<style>
.block-container { padding-top: 1rem; padding-bottom: 1rem; }
</style>
""",
    unsafe_allow_html=True,
)

# ----------------------------
# Event log structure
# ----------------------------
@dataclass
class EventLogItem:
    t_sec: float
    level: str
    message: str


# ----------------------------
# Init dispatcher state
# ----------------------------
def _init_dispatcher_state(run_id: str):
    if "run_id" not in st.session_state:
        st.session_state.run_id = run_id

    if "asset_roles" not in st.session_state:
        st.session_state.asset_roles = {}  # track_id -> role string

    if "task_hist" not in st.session_state:
        st.session_state.task_hist = {}  # task -> {"status","since","last_seen"}

    if "task_counters" not in st.session_state:
        st.session_state.task_counters = {}  # task -> {"on","off"}

    # persistent alerts store (dedupe + escalation)
    if "alerts" not in st.session_state:
        st.session_state.alerts: Dict[str, AlertItem] = {}

    if "event_log" not in st.session_state:
        st.session_state.event_log: List[EventLogItem] = []

    if "playback_running" not in st.session_state:
        st.session_state.playback_running = False
    if "playback_idx" not in st.session_state:
        st.session_state.playback_idx = 0

    if "tracker" not in st.session_state:
        st.session_state.tracker = SimpleIoUTracker(iou_match=0.35, max_missed=10)

    # Sequence state machine
    if "seq_state" not in st.session_state:
        st.session_state.seq_state = SequenceState()
    if "seq_done_sensitivity" not in st.session_state:
        st.session_state.seq_done_sensitivity = 5.0  # seconds


def _log(level: str, t_sec: float, msg: str):
    st.session_state.event_log.insert(0, EventLogItem(t_sec=float(t_sec), level=level, message=str(msg)))
    st.session_state.event_log = st.session_state.event_log[:250]


def _upsert_alert(alert_id: str, severity: str, rule_id: str, message: str, now_t: float):
    """
    Store alerts in st.session_state.alerts with dedupe.
    """
    alerts: Dict[str, AlertItem] = st.session_state.alerts
    if alert_id in alerts:
        a = alerts[alert_id]
        a.last_seen = float(now_t)
        # escalate if needed
        sev_rank = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
        if sev_rank.get(severity, 0) > sev_rank.get(a.severity, 0):
            a.severity = severity
            a.message = message
        alerts[alert_id] = a
    else:
        alerts[alert_id] = AlertItem(
            alert_id=alert_id,
            severity=severity,
            rule_id=rule_id,
            message=message,
            first_seen=float(now_t),
            last_seen=float(now_t),
        )


# ----------------------------
# Asset Tagging UI (A)
# ----------------------------
ROLE_OPTIONS = {
    "UNASSIGNED": "Unassigned",
    "FUEL_TRUCK": "Fuel Truck",
    "GPU_TRUCK": "GPU",
    "BELT_LOADER": "Belt / Baggage",
    "PUSHBACK_TUG": "Tug / Pushback",
    "STAIRS": "Stairs (proxy)",
    "OTHER": "Other",
}


def render_asset_tagging(dets_df: pd.DataFrame):
    if dets_df is None or dets_df.empty:
        st.info("No detections in current frame.")
        return

    # Vehicles only (proxy)
    if "cls_name" in dets_df.columns:
        veh = dets_df[dets_df["cls_name"].isin(["truck", "car", "bus", "train"])].copy()
    else:
        veh = dets_df.copy()

    if veh.empty:
        st.info("No vehicle-class detections (truck/car/...).")
        return

    # sort by confidence
    if "conf" in veh.columns:
        veh = veh.sort_values("conf", ascending=False)

    # one row per track_id: keep best conf
    if "track_id" in veh.columns and "conf" in veh.columns:
        veh = veh.sort_values(["track_id", "conf"], ascending=[True, False]).drop_duplicates("track_id")

    for _, r in veh.iterrows():
        tid = int(r["track_id"]) if "track_id" in r else -1
        cls_name = str(r.get("cls_name", "vehicle"))
        conf = float(r.get("conf", 0.0))

        current = st.session_state.asset_roles.get(tid, "UNASSIGNED")

        c1, c2 = st.columns([1.1, 1.4], gap="small")
        with c1:
            st.markdown(f"**{cls_name}** `id={tid}`")
            st.caption(f"conf {conf:.2f}")

        with c2:
            new_role = st.selectbox(
                label="",
                options=list(ROLE_OPTIONS.keys()),
                index=list(ROLE_OPTIONS.keys()).index(current) if current in ROLE_OPTIONS else 0,
                key=f"tag_{tid}",
                label_visibility="collapsed",
            )
            if new_role != current:
                st.session_state.asset_roles[tid] = new_role
                _log("info", st.session_state.t_sec, f"Asset tagged: id={tid} as {ROLE_OPTIONS.get(new_role, new_role)}")
                st.rerun()


# ----------------------------
# Header
# ----------------------------
st.title("PortHub Royale — Dispatcher Desk")

run_id = "run-0001"
_init_dispatcher_state(run_id)

# ----------------------------
# UI - Sidebar
# ----------------------------
with st.sidebar:
    st.header("Input")
    frames_folder = st.text_input("Frames folder", value="data/frames_1fps")

    st.header("Playback (Live view)")
    playback_fps = st.slider("Playback FPS", 1, 12, 4)
    loop_playback = st.checkbox("Loop", value=True)

    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("Play"):
            st.session_state.playback_running = True
            st.rerun()
    with b2:
        if st.button("Pause"):
            st.session_state.playback_running = False
            st.rerun()
    with b3:
        if st.button("Reset"):
            st.session_state.playback_running = False
            st.session_state.playback_idx = 0
            # reset volatile state for a clean run
            st.session_state.task_hist = {}
            st.session_state.task_counters = {}
            st.session_state.alerts = {}
            st.session_state.tracker = SimpleIoUTracker(iou_match=0.35, max_missed=10)
            st.session_state.seq_state = SequenceState()
            _log("info", st.session_state.t_sec if "t_sec" in st.session_state else 0.0, "Playback reset")
            st.rerun()

    st.header("Detection")
    demo_mode = st.checkbox("Demo Mode (no ML deps)", value=False)
    conf = st.slider("Confidence", 0.05, 0.95, 0.35, 0.01)
    iou = st.slider("IoU", 0.05, 0.95, 0.50, 0.01)
    weights = st.text_input("YOLO weights", value="yolov8n.pt")

    st.header("ROIs (x1,y1,x2,y2)")
    show_rois = st.checkbox("Show ROIs overlay", value=False)
    roi_nose = st.text_input("nose", value="260,250,620,520")
    roi_fuel = st.text_input("fuel", value="620,170,980,520")
    roi_belly = st.text_input("belly", value="250,320,520,650")
    roi_aircraft = st.text_input("aircraft", value="250,120,980,690")
    roi_engine = st.text_input("engine", value="330,300,620,560")

# Parse ROIs
rois: Dict[str, Optional[Tuple[int, int, int, int]]] = {
    "nose": parse_roi(roi_nose),
    "fuel": parse_roi(roi_fuel),
    "belly": parse_roi(roi_belly),
    "aircraft": parse_roi(roi_aircraft),
    "engine": parse_roi(roi_engine),
}

# ----------------------------
# Load frames
# ----------------------------
frames = list_frames(frames_folder)
if not frames:
    st.error("No frames found. Check Frames folder path.")
    st.stop()

idx = int(st.session_state.playback_idx)
idx = max(0, min(idx, len(frames) - 1))
frame_path = frames[idx]
t_sec = float(idx)  # 1fps assumption
st.session_state.t_sec = t_sec

img = Image.open(frame_path).convert("RGB")

# ----------------------------
# Detection + tracking
# ----------------------------
model = None
if not demo_mode:

    @st.cache_resource
    def _cached_model(w: str):
        return load_model(w)

    model = _cached_model(weights)

raw = demo_detections(img, t_sec) if demo_mode else yolo_detect(model, img, conf=conf, iou=iou)
tracked = st.session_state.tracker.update(raw)
dets_df = detections_df_from_tracked(tracked)

# ----------------------------
# Evaluate tasks (Rule Engine)
# ----------------------------
eval_tasks(
    dets_df=dets_df,
    rois=rois,
    t_sec=t_sec,
    asset_roles=st.session_state.asset_roles,
    task_hist=st.session_state.task_hist,
    task_counters=st.session_state.task_counters,
    log=_log,
)

# ----------------------------
# Sequence State Machine (GPU → Fuel → Baggage → Pushback)
# ----------------------------
seq = update_sequence(
    now_t=t_sec,
    task_hist=st.session_state.task_hist,
    seq_state=st.session_state.seq_state,
    alert=lambda alert_id, severity, rule_id, message, now_t: _upsert_alert(alert_id, severity, rule_id, message, now_t),
    min_active_sec_for_done=float(st.session_state.seq_done_sensitivity),
)

# ----------------------------
# Layout (Frame + Console)
# ----------------------------
left, right = st.columns([3.6, 1.7], gap="large")

with left:
    st.markdown("## Live Camera Feed (frames playback)")
    title = f"Flight LX-123 (Gate A12) · Run {st.session_state.run_id} · t={t_sec:.0f}s · {frame_path.name}"
    overlay_rois = rois if show_rois else None
    overlay = draw_overlay(img, dets_df, title=title, rois=overlay_rois, asset_roles=st.session_state.asset_roles)
    st.image(overlay, width="stretch")

with right:
    st.markdown("## Dispatcher Console")
    st.markdown("**Turnaround**")
    st.caption(f"Flight: **LX-123** (Gate A12) · Run: `{st.session_state.run_id}`")

    st.markdown("### Asset tagging (human-in-the-loop)")
    st.caption("Tag detected vehicles once → tasks become realistic.")
    render_asset_tagging(dets_df)

# ----------------------------
# Tabs under frame
# ----------------------------
st.markdown("---")
tabs = st.tabs(["Turnaround Operations", "Alerts", "Event log", "Timeline"])

def _seq_step_status(step_key: str, requires: List[str], now_t: float, deadline: Optional[float]) -> Tuple[str, str]:
    """
    Returns: (status_label, hint_text)
    """
    th = st.session_state.task_hist.get(step_key, {})
    status = str(th.get("status", "NOT_STARTED"))

    done = (step_key in st.session_state.seq_state.done_at) or (status == "DONE")
    active = (status == "ACTIVE")

    # prereq done?
    prereq_done = all(
        (k in st.session_state.seq_state.done_at) or (str(st.session_state.task_hist.get(k, {}).get("status")) == "DONE")
        for k in (requires or [])
    )

    if done:
        return "DONE", "completed"
    if active:
        return "ACTIVE", "in progress"
    if not prereq_done:
        return "BLOCKED", "waiting for prerequisites"
    if deadline is not None and now_t >= float(deadline) and (step_key not in st.session_state.seq_state.started_at):
        return "OVERDUE", f"deadline t={float(deadline):.0f}s"
    return "WAITING", "next in queue"

def _pill(label: str) -> Tuple[str, str]:
    # label -> (display, color)
    if label == "DONE":
        return "DONE", "#1f7a3a"
    if label == "ACTIVE":
        return "ACTIVE", "#1f7a3a"
    if label == "OVERDUE":
        return "OVERDUE", "#7a1f1f"
    if label == "BLOCKED":
        return "BLOCKED", "#3b3f46"
    return "WAITING", "#3b3f46"

with tabs[0]:
    # Banner + sensitivity control
    cL, cR = st.columns([3.2, 1.0], gap="large")
    with cL:
        next_title = "All steps completed"
        if st.session_state.seq_state.current_idx < len(seq):
            next_title = seq[st.session_state.seq_state.current_idx].title
        st.info(f"**Next expected step: {next_title}**")

    with cR:
        st.session_state.seq_done_sensitivity = st.slider(
            "DONE sensitivity",
            min_value=1.0,
            max_value=20.0,
            value=float(st.session_state.seq_done_sensitivity),
            step=0.5,
        )

    # Progress
    done_count = 0
    for s in seq:
        th = st.session_state.task_hist.get(s.key, {})
        if (s.key in st.session_state.seq_state.done_at) or (str(th.get("status")) == "DONE"):
            done_count += 1
    st.progress(done_count / max(1, len(seq)))

    st.markdown("### Sequence")
    for step in seq:
        label, hint = _seq_step_status(step.key, step.requires_done, t_sec, step.deadline_sec)
        pill_txt, color = _pill(label)

        left2, right2 = st.columns([4, 1], gap="small")
        with left2:
            st.subheader(step.title)
            # show timing hints
            extra = []
            if step.deadline_sec is not None:
                if t_sec < float(step.deadline_sec):
                    extra.append(f"deadline in {float(step.deadline_sec - t_sec):.0f}s")
                else:
                    extra.append(f"deadline passed by {float(t_sec - step.deadline_sec):.0f}s")
            if step.key in st.session_state.seq_state.started_at:
                extra.append(f"started at {st.session_state.seq_state.started_at[step.key]:.0f}s")
            if step.key in st.session_state.seq_state.done_at:
                extra.append(f"done at {st.session_state.seq_state.done_at[step.key]:.0f}s")

            st.caption(" · ".join([hint] + extra) if extra else hint)

        with right2:
            st.markdown(
                f"<div style='text-align:right; padding-top:8px;'>"
                f"<span style='background:{color};padding:8px 12px;border-radius:999px;font-weight:600;'>{pill_txt}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

with tabs[1]:
    alerts_df = compute_alerts_df(
        now_t=t_sec,
        task_hist=st.session_state.task_hist,
        alerts=st.session_state.alerts,
        log=_log,
    )
    if alerts_df is None or alerts_df.empty:
        st.success("No alerts in current window.")
    else:
        st.dataframe(alerts_df, width="stretch", height=280)

with tabs[2]:
    if st.session_state.event_log:
        for e in st.session_state.event_log[:80]:
            badge = "⚠️" if e.level == "warning" else ("✅" if e.level == "task" else "ℹ️")
            st.write(f"{badge} **t={e.t_sec:.1f}s** — {e.message}")
    else:
        st.info("No events yet.")

with tabs[3]:
    rows = []
    for t in TASKS:
        h = st.session_state.task_hist.get(t["key"], {"status": "NOT_STARTED", "since": None, "last_seen": None})
        rows.append(
            {
                "task": t["title"],
                "status": h.get("status"),
                "since_sec": h.get("since"),
                "last_seen_sec": h.get("last_seen"),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", height=260)

# ----------------------------
# Playback loop
# ----------------------------
if st.session_state.playback_running:
    time.sleep(1.0 / max(1, int(playback_fps)))
    nxt = idx + 1
    if nxt >= len(frames):
        if loop_playback:
            nxt = 0
            # loop restart: reset volatile per-run state (keeps tagging)
            st.session_state.task_hist = {}
            st.session_state.task_counters = {}
            st.session_state.alerts = {}
            st.session_state.tracker = SimpleIoUTracker(iou_match=0.35, max_missed=10)
            st.session_state.seq_state = SequenceState()
        else:
            nxt = len(frames) - 1
            st.session_state.playback_running = False
    st.session_state.playback_idx = nxt
    st.rerun()
