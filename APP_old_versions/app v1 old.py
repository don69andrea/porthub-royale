# app.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from PIL import Image

from streamlit_autorefresh import st_autorefresh

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
from src.turnaround_sequence import SequenceState, update_sequence, default_sequence


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

    if "alerts" not in st.session_state:
        st.session_state.alerts = {}  # alert_id -> AlertItem

    if "event_log" not in st.session_state:
        st.session_state.event_log: List[EventLogItem] = []

    if "playback_running" not in st.session_state:
        st.session_state.playback_running = False
    if "playback_idx" not in st.session_state:
        st.session_state.playback_idx = 0

    if "tracker" not in st.session_state:
        st.session_state.tracker = SimpleIoUTracker(iou_match=0.35, max_missed=10)

    # Sequence
    if "seq_state" not in st.session_state:
        st.session_state.seq_state = SequenceState()
    if "seq_min_active_sec" not in st.session_state:
        st.session_state.seq_min_active_sec = 5.0

    # (2) Track stability memory (prevents flicker in asset tagging)
    if "track_seen" not in st.session_state:
        st.session_state.track_seen = {}  # track_id -> {"count": int, "last_t": float}


def _log(level: str, t_sec: float, msg: str):
    st.session_state.event_log.insert(0, EventLogItem(t_sec=float(t_sec), level=level, message=str(msg)))
    st.session_state.event_log = st.session_state.event_log[:250]


def _reset_run_state(keep_asset_roles: bool = True):
    """
    (3) Reset all state that can cause 'sticky' alerts/ROI overlay across loops.
    Keep asset roles by default (so human tagging survives resets).
    """
    st.session_state.task_hist = {}
    st.session_state.task_counters = {}
    st.session_state.alerts = {}
    st.session_state.seq_state = SequenceState()
    st.session_state.tracker = SimpleIoUTracker(iou_match=0.35, max_missed=10)
    st.session_state.track_seen = {}
    if not keep_asset_roles:
        st.session_state.asset_roles = {}
    _log("info", st.session_state.t_sec if "t_sec" in st.session_state else 0.0, "Run state reset")


def _alert_upsert_seq(alert_id: str, severity: str, rule_id: str, message: str, now_t: float):
    a = st.session_state.alerts.get(alert_id)
    if a is None:
        st.session_state.alerts[alert_id] = AlertItem(
            alert_id=alert_id,
            severity=severity,
            rule_id=rule_id,
            message=message,
            first_seen=now_t,
            last_seen=now_t,
            status="OPEN",
        )
        _log("warning" if severity in ("WARNING", "CRITICAL") else "info", now_t, f"{severity}: {message}")
    else:
        a.last_seen = now_t
        sev_rank = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
        if sev_rank.get(severity, 0) > sev_rank.get(a.severity, 0):
            a.severity = severity
            a.message = message


# ----------------------------
# Asset Tagging UI
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

    if "cls_name" in dets_df.columns:
        veh = dets_df[dets_df["cls_name"].isin(["truck", "car", "bus", "train"])].copy()
    else:
        veh = dets_df.copy()

    if veh.empty:
        st.info("No vehicle-class detections (truck/car/...).")
        return

    # (2) Stability filter to avoid flicker:
    # - must be seen >=2 times
    # - must be seen in last 2 seconds
    def _stable(tid: int) -> bool:
        rec = st.session_state.track_seen.get(tid)
        if not rec:
            return False
        return int(rec.get("count", 0)) >= 2 and (float(st.session_state.t_sec) - float(rec.get("last_t", -999))) <= 2.0

    if "track_id" in veh.columns:
        veh = veh[veh["track_id"].apply(lambda x: _stable(int(x)))]
        if veh.empty:
            st.info("Vehicles detected, but not stable yet (needs 2 frames).")
            return

    if "conf" in veh.columns:
        veh = veh.sort_values("conf", ascending=False)

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
# UI - Sidebar
# ----------------------------
st.title("PortHub Royale — Dispatcher Desk")

run_id = "run-0001"
_init_dispatcher_state(run_id)

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
    with b2:
        if st.button("Pause"):
            st.session_state.playback_running = False
    with b3:
        if st.button("Reset"):
            st.session_state.playback_running = False
            st.session_state.playback_idx = 0
            _reset_run_state(keep_asset_roles=True)
            st.rerun()

    st.header("Detection")
    demo_mode = st.checkbox("Demo Mode (no ML deps)", value=False)
    run_detection_while_playing = st.checkbox("Run detection during Play", value=False)
    conf = st.slider("Confidence", 0.05, 0.95, 0.35, 0.01)
    iou = st.slider("IoU", 0.05, 0.95, 0.50, 0.01)
    weights = st.text_input("YOLO weights", value="yolov8n.pt")

    show_rois = st.checkbox("Show ROIs overlay", value=False)

    st.header("ROIs (x1,y1,x2,y2)")
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

with st.sidebar:
    st.caption(f"Frames found: **{len(frames)}**")
    st.caption(f"Current idx: **{int(st.session_state.playback_idx)}**")

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
do_detection = (not st.session_state.playback_running) or bool(run_detection_while_playing)

if not demo_mode and do_detection:

    @st.cache_resource
    def _cached_model(w: str):
        return load_model(w)

    model = _cached_model(weights)

if demo_mode:
    raw = demo_detections(img, t_sec)
elif do_detection:
    raw = yolo_detect(model, img, conf=conf, iou=iou)
else:
    raw = []

tracked = st.session_state.tracker.update(raw) if raw else []
dets_df = detections_df_from_tracked(tracked) if tracked else pd.DataFrame()

# (2) Update stability memory
if dets_df is not None and not dets_df.empty and "track_id" in dets_df.columns:
    for tid in dets_df["track_id"].dropna().astype(int).tolist():
        rec = st.session_state.track_seen.get(tid, {"count": 0, "last_t": -1.0})
        rec["count"] = int(rec.get("count", 0)) + 1
        rec["last_t"] = float(t_sec)
        st.session_state.track_seen[tid] = rec


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

# Sequence updates (already in your project)
update_sequence(
    now_t=t_sec,
    task_hist=st.session_state.task_hist,
    seq_state=st.session_state.seq_state,
    alert=_alert_upsert_seq,
    min_active_sec_for_done=float(st.session_state.seq_min_active_sec),
)

alerts_df = compute_alerts_df(
    now_t=t_sec,
    task_hist=st.session_state.task_hist,
    alerts=st.session_state.alerts,
    log=_log,
)

# Auto show ROIs only while there are active alerts (but alerts reset on loop start now)
auto_show_rois = alerts_df is not None and not alerts_df.empty


# ----------------------------
# Layout (Frame + Console)
# ----------------------------
left, right = st.columns([3.6, 1.7], gap="large")

with left:
    st.markdown("## Live Camera Feed (frames playback)")
    title = f"Flight LX-123 (Gate A12) · Run {st.session_state.run_id} · t={t_sec:.0f}s · {frame_path.name}"

    show_rois_effective = bool(show_rois) or bool(auto_show_rois)

    overlay = draw_overlay(
        img,
        dets_df,
        title=title,
        rois=rois if show_rois_effective else {},
        asset_roles=st.session_state.asset_roles,
    )
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

with tabs[0]:
    seq = default_sequence()
    sidx = int(st.session_state.seq_state.current_idx)

    cL, cR = st.columns([2.2, 1], gap="small")
    with cL:
        if sidx < len(seq):
            st.info(f"Next expected step: **{seq[sidx].title}**")
        else:
            st.success("Sequence completed ✅")
    with cR:
        st.caption("DONE sensitivity")
        st.session_state.seq_min_active_sec = st.slider(
            "min ACTIVE seconds to mark DONE",
            1.0, 20.0,
            value=float(st.session_state.seq_min_active_sec),
            step=1.0,
            label_visibility="collapsed",
        )

    st.markdown("#### Sequence")
    for i, step in enumerate(seq):
        started = st.session_state.seq_state.started_at.get(step.key)
        done = st.session_state.seq_state.done_at.get(step.key)

        if done is not None:
            badge = "✅ DONE"
        elif started is not None:
            badge = "🟡 IN PROGRESS"
        elif i == sidx:
            badge = "➡️ NEXT"
        else:
            badge = "⏳ PENDING"
        st.write(f"**{step.title}** — {badge}")

    st.markdown("---")

    for t in TASKS:
        key = t["key"]
        title = t["title"]
        h = st.session_state.task_hist.get(key, {"status": "NOT_STARTED", "since": None, "last_seen": None})

        status = h.get("status", "NOT_STARTED")
        since = h.get("since")
        last_seen = h.get("last_seen")

        c1, c2 = st.columns([3, 1], gap="small")
        with c1:
            st.subheader(title)
            if last_seen is not None and since is not None:
                st.caption(f"since {since:.0f}s · last seen {max(0.0, t_sec - last_seen):.0f}s ago")
            else:
                st.caption("not seen yet")

        with c2:
            pill = "ACTIVE" if status == "ACTIVE" else ("INACTIVE" if status == "INACTIVE" else "NOT_STARTED")
            color = "#1f7a3a" if pill == "ACTIVE" else ("#7a1f1f" if pill == "INACTIVE" else "#3b3f46")
            st.markdown(
                f"<div style='text-align:right; padding-top:8px;'>"
                f"<span style='background:{color};padding:8px 12px;border-radius:999px;font-weight:600;'>{pill}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

with tabs[1]:
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
# Playback tick (NO sleep/rerun)
# ----------------------------
if st.session_state.playback_running:
    st_autorefresh(
        interval=int(1000 / max(1, int(playback_fps))),
        key="playback_tick",
    )

    nxt = idx + 1
    if nxt >= len(frames):
        if loop_playback:
            nxt = 0
            # (3) on loop wrap: reset run-state so alerts/ROIs/tracker don't stick
            _reset_run_state(keep_asset_roles=True)
        else:
            nxt = len(frames) - 1
            st.session_state.playback_running = False

    st.session_state.playback_idx = nxt
# ----------------------------
