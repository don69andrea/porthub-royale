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

from src.rules_engine import eval_tasks, compute_alerts_df, AlertItem
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
# Utilities
# ----------------------------
@dataclass
class EventLogItem:
    t_sec: float
    level: str
    msg: str


def _log(level: str, t_sec: float, msg: str):
    st.session_state.event_log.append(EventLogItem(t_sec=float(t_sec), level=str(level), msg=str(msg)))


def _init_dispatcher_state(run_id: str):
    if "run_id" not in st.session_state:
        st.session_state.run_id = run_id

    if "asset_roles" not in st.session_state:
        st.session_state.asset_roles = {}  # track_id -> role string

    # remember last known bbox per tagged ROLE (for role-handoff when track_id changes)
    if "role_memory" not in st.session_state:
        st.session_state.role_memory = {}  # role -> {"track_id": int, "bbox": (x1,y1,x2,y2), "last_seen": float}

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

    # tracker (make it more tolerant)
    if "tracker" not in st.session_state:
        st.session_state.tracker = SimpleIoUTracker(iou_match=0.25, max_missed=40)

    # Sequence state machine
    if "seq_state" not in st.session_state:
        st.session_state.seq_state = SequenceState()

    if "seq_done_sensitivity" not in st.session_state:
        st.session_state.seq_done_sensitivity = 6.0

    if "t_sec" not in st.session_state:
        st.session_state.t_sec = 0.0

    # Dashboard stability (prevent flickering)
    if "dashboard_state" not in st.session_state:
        st.session_state.dashboard_state = {
            "active_tasks_display": "",
            "active_tasks_last_change": 0.0,
            "stability_delay": 5.0,  # Show state for minimum 5 seconds
        }

    # Passenger flow state
    if "passenger_flow_state" not in st.session_state:
        from src.passenger_flow import PassengerFlowState
        st.session_state.passenger_flow_state = PassengerFlowState()

    # Fingerdock state
    if "fingerdock_state" not in st.session_state:
        from src.fingerdock_detection import FingerdockState
        st.session_state.fingerdock_state = FingerdockState()


def _upsert_alert(alert_id: str, severity: str, rule_id: str, message: str, now_t: float):
    a = st.session_state.alerts.get(alert_id)
    if a is None:
        st.session_state.alerts[alert_id] = AlertItem(
            alert_id=alert_id,
            severity=severity,
            rule_id=rule_id,
            message=message,
            first_seen=float(now_t),
            last_seen=float(now_t),
        )
        _log("alert", now_t, f"[{severity}] {message}")
    else:
        a.last_seen = float(now_t)
        sev_rank = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
        if sev_rank.get(severity, 0) > sev_rank.get(a.severity, 0):
            a.severity = severity
        a.message = message
        st.session_state.alerts[alert_id] = a


# ----------------------------
# Role handoff (keeps tagging stable even if track_id changes)
# ----------------------------
def _bbox_iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0
    inter = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    area_a = max(1, (ax2 - ax1)) * max(1, (ay2 - ay1))
    area_b = max(1, (bx2 - bx1)) * max(1, (by2 - by1))
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


def _vehicles_df(dets_df: pd.DataFrame) -> pd.DataFrame:
    if dets_df is None or dets_df.empty:
        return pd.DataFrame()
    if "cls_name" in dets_df.columns:
        return dets_df[dets_df["cls_name"].isin(["truck", "car", "bus", "train"])].copy()
    return dets_df.copy()


def _update_role_memory(dets_df: pd.DataFrame, now_t: float):
    """
    Update memory bbox for roles that are currently tagged and visible in this frame.
    """
    veh = _vehicles_df(dets_df)
    if veh.empty or "track_id" not in veh.columns:
        return

    by_tid = {int(r["track_id"]): tuple(r["bbox_xyxy"]) for _, r in veh.iterrows()}
    for tid, role in list(st.session_state.asset_roles.items()):
        if role in ("UNASSIGNED", "", None):
            continue
        if tid in by_tid:
            st.session_state.role_memory[role] = {
                "track_id": int(tid),
                "bbox": by_tid[tid],
                "last_seen": float(now_t),
            }


def _role_handoff(dets_df: pd.DataFrame, now_t: float, iou_thr: float = 0.20, max_age_sec: float = 8.0):
    """
    If a tagged role disappears because the track_id changed, try to re-attach that role
    to the best matching new track based on bbox IoU against recent role_memory.
    """
    if "role_memory" not in st.session_state or not st.session_state.role_memory:
        return

    veh = _vehicles_df(dets_df)
    if veh.empty or "track_id" not in veh.columns:
        return

    present_tids = set(int(x) for x in veh["track_id"].dropna().tolist())
    present_roles = set()
    for tid in present_tids:
        r = st.session_state.asset_roles.get(int(tid), "UNASSIGNED")
        if r and r != "UNASSIGNED":
            present_roles.add(r)

    # candidates: unassigned vehicles in this frame
    cand = veh.copy()
    cand["track_id"] = cand["track_id"].astype(int)

    assigned_tids = [tid for tid, role in st.session_state.asset_roles.items() if role and role != "UNASSIGNED"]
    cand = cand[~cand["track_id"].isin(assigned_tids)].copy()
    if cand.empty:
        return

    for role, mem in list(st.session_state.role_memory.items()):
        if role in ("UNASSIGNED", "", None, "OTHER"):
            continue
        if role in present_roles:
            continue
        if float(now_t) - float(mem.get("last_seen", -1e9)) > float(max_age_sec):
            continue

        mem_bbox = tuple(mem["bbox"])
        best_iou = 0.0
        best_tid = None
        best_bbox = None

        for _, r in cand.iterrows():
            bbox = tuple(r["bbox_xyxy"])
            i = _bbox_iou(mem_bbox, bbox)
            if i > best_iou:
                best_iou = i
                best_tid = int(r["track_id"])
                best_bbox = bbox

        if best_tid is not None and best_iou >= float(iou_thr):
            old_tid = int(mem.get("track_id", -1))

            # remove old mapping if it still points to this role
            if old_tid in st.session_state.asset_roles and st.session_state.asset_roles.get(old_tid) == role:
                del st.session_state.asset_roles[old_tid]

            st.session_state.asset_roles[best_tid] = role
            st.session_state.role_memory[role] = {"track_id": best_tid, "bbox": best_bbox, "last_seen": float(now_t)}
            _log("info", now_t, f"Role handoff: {role} moved {old_tid} → {best_tid} (IoU {best_iou:.2f})")


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

    # one row per track_id: keep best conf (robust)
    if "track_id" in veh.columns:
        if "conf" in veh.columns:
            veh = (
                veh.sort_values("conf", ascending=False)
                .groupby("track_id", as_index=False)
                .first()
            )
        else:
            veh = veh.groupby("track_id", as_index=False).first()

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
    frames_folder = st.text_input("Frames folder", value="data/frames")

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
            # reset volatile state for a clean run (but keep model + UI)
            st.session_state.task_hist = {}
            st.session_state.task_counters = {}
            st.session_state.alerts = {}
            st.session_state.seq_state = SequenceState()
            st.session_state.tracker = SimpleIoUTracker(iou_match=0.25, max_missed=40)

            # keep tagging? (YES) — but clear role memory for clean handoff behavior
            st.session_state.role_memory = {}

            _log("info", st.session_state.t_sec if "t_sec" in st.session_state else 0.0, "Playback reset")
            st.rerun()

    st.header("Detection")
    demo_mode = st.checkbox("Demo Mode (no ML deps)", value=False)
    conf = st.slider("Confidence", 0.05, 0.95, 0.20, 0.01)
    iou = st.slider("IoU", 0.05, 0.95, 0.50, 0.01)
    weights = st.text_input("YOLO weights", value="yolov8n.pt")

    st.header("ROIs (x1,y1,x2,y2)")
    show_rois = st.checkbox("Show ROIs overlay", value=False)

    st.header("Overlay Filter")
    overlay_classes = st.multiselect(
        "Show classes",
        options=["person", "truck", "car", "bus", "train", "airplane"],
        default=["airplane", "person", "truck", "car", "bus", "train"],
    )

    roi_nose = st.text_input("nose", value="260,250,620,520")
    roi_fuel = st.text_input("fuel", value="620,170,980,520")
    roi_belly = st.text_input("belly", value="250,320,520,650")
    roi_aircraft = st.text_input("aircraft", value="250,120,980,690")
    roi_engine = st.text_input("engine", value="330,300,620,560")
    roi_pushback = st.text_input("pushback", value="120,420,330,680")
    roi_passenger_door = st.text_input("passenger_door (fingerdock)", value="920,140,1120,380")
    roi_fingerdock = st.text_input("fingerdock", value="820,160,1080,500")

# Parse ROIs
rois: Dict[str, Optional[Tuple[int, int, int, int]]] = {
    "nose": parse_roi(roi_nose),
    "fuel": parse_roi(roi_fuel),
    "belly": parse_roi(roi_belly),
    "aircraft": parse_roi(roi_aircraft),
    "engine": parse_roi(roi_engine),
    "pushback": parse_roi(roi_pushback),
    "passenger_door": parse_roi(roi_passenger_door),
    "fingerdock": parse_roi(roi_fingerdock),
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

if demo_mode:
    dets = demo_detections(t_sec=t_sec)
else:
    dets = yolo_detect(model=model, img=img, conf=conf, iou=iou)

tracked = st.session_state.tracker.update(dets)
dets_df = detections_df_from_tracked(tracked)

# --- DEBUG: class counts (quick truth check) ---
debug_det = st.sidebar.checkbox("Debug detections", value=False)

if debug_det and dets_df is not None and not dets_df.empty:
    st.sidebar.markdown("### Debug: detections per class")
    if "cls_name" in dets_df.columns:
        counts = dets_df.groupby("cls_name").size().sort_values(ascending=False)
        st.sidebar.dataframe(counts)

        plane = dets_df[dets_df["cls_name"] == "airplane"]
        if not plane.empty and "conf" in plane.columns:
            st.sidebar.success(f"airplane detections: {len(plane)} (max conf {plane['conf'].max():.2f})")
        else:
            st.sidebar.warning("airplane detections: 0")
    else:
        st.sidebar.info("No cls_name column in detections dataframe.")

# keep tagging stable even if track_ids change
_update_role_memory(dets_df, now_t=t_sec)
_role_handoff(dets_df, now_t=t_sec)

# ----------------------------
# Passenger flow & fingerdock detection (NEW)
# ----------------------------
from src.passenger_flow import update_passenger_flow
from src.fingerdock_detection import detect_fingerdock

# Detect passenger movement direction and flow status
passenger_flow_status = update_passenger_flow(
    dets_df=dets_df,
    roi_passenger_door=rois.get("passenger_door"),
    t_sec=t_sec,
    state=st.session_state.passenger_flow_state,
)

# Update task_hist with passenger flow statuses
for task_key, status in passenger_flow_status.items():
    if task_key in st.session_state.task_hist:
        prev_status = st.session_state.task_hist[task_key].get("status")
        if prev_status != status:
            st.session_state.task_hist[task_key]["status"] = status
            st.session_state.task_hist[task_key]["since"] = t_sec
            _log("task", t_sec, f"{task_key} => {status}")
        st.session_state.task_hist[task_key]["last_seen"] = t_sec
    else:
        st.session_state.task_hist[task_key] = {
            "status": status,
            "since": t_sec,
            "last_seen": t_sec
        }

# Detect fingerdock position
fingerdock_status = detect_fingerdock(
    dets_df=dets_df,
    roi_fingerdock=rois.get("fingerdock"),
    t_sec=t_sec,
    state=st.session_state.fingerdock_state,
)

# Update task_hist with fingerdock status
task_key = "fingerdock"
if task_key in st.session_state.task_hist:
    prev_status = st.session_state.task_hist[task_key].get("status")
    if prev_status != fingerdock_status:
        st.session_state.task_hist[task_key]["status"] = fingerdock_status
        st.session_state.task_hist[task_key]["since"] = t_sec
        _log("task", t_sec, f"{task_key} => {fingerdock_status}")
    st.session_state.task_hist[task_key]["last_seen"] = t_sec
else:
    st.session_state.task_hist[task_key] = {
        "status": fingerdock_status,
        "since": t_sec,
        "last_seen": t_sec
    }

# ----------------------------
# Evaluate tasks + sequence + alerts
# ----------------------------
eval_tasks(
    dets_df=dets_df,
    rois=rois,
    t_sec=t_sec,
    asset_roles=st.session_state.asset_roles,
    task_hist=st.session_state.task_hist,
    task_counters=st.session_state.task_counters,
    log=lambda level, now_t, msg: _log(level, now_t, msg),
)

seq = update_sequence(
    now_t=t_sec,
    task_hist=st.session_state.task_hist,
    seq_state=st.session_state.seq_state,
    alert=lambda alert_id, severity, rule_id, message, now_t: _upsert_alert(alert_id, severity, rule_id, message, now_t),
    min_active_sec_for_done=float(st.session_state.seq_done_sensitivity),
)

alerts_df = compute_alerts_df(
    now_t=t_sec,
    task_hist=st.session_state.task_hist,
    alerts=st.session_state.alerts,
    log=lambda level, now_t, msg: _log(level, now_t, msg),
)

# ----------------------------
# Layout (Frame + Console)
# ----------------------------
left, right = st.columns([3.6, 1.7], gap="large")

with left:
    st.markdown("## Live Camera Feed (frames playback)")

    # ----------------------------
    # STATUS DASHBOARD BAR (above live video)
    # Fixed height to prevent layout shift
    # ----------------------------
    # Count active tasks and safety alerts
    active_tasks = []
    for task_key, task_data in st.session_state.task_hist.items():
        if task_data.get("status") in ["ACTIVE", "STARTED", "ONGOING"]:
            active_tasks.append(task_key.replace("_", " ").title())

    critical_alerts = sum(1 for a in st.session_state.alerts.values() if a.severity == "CRITICAL" and a.status == "ACTIVE")
    warning_alerts = sum(1 for a in st.session_state.alerts.values() if a.severity == "WARNING" and a.status == "ACTIVE")

    # Total detections count
    total_detections = len(dets_df) if dets_df is not None and not dets_df.empty else 0
    person_count = len(dets_df[dets_df["cls_name"] == "person"]) if dets_df is not None and not dets_df.empty and "cls_name" in dets_df.columns else 0
    vehicle_count = len(dets_df[dets_df["cls_name"].isin(["truck", "car", "bus"])]) if dets_df is not None and not dets_df.empty and "cls_name" in dets_df.columns else 0

    # Sequence progress (percentage)
    seq_progress = 0
    if hasattr(st.session_state.seq_state, 'steps') and st.session_state.seq_state.steps:
        done_count = sum(1 for step in st.session_state.seq_state.steps if step["key"] in st.session_state.seq_state.done_at)
        seq_progress = int((done_count / len(st.session_state.seq_state.steps)) * 100)

    # STABILITY: Prevent flickering with 5-second minimum display time
    current_active_str = ""
    if active_tasks:
        current_active_str = ", ".join(active_tasks[:2])
        if len(active_tasks) > 2:
            current_active_str += f" +{len(active_tasks)-2}"

    dash_state = st.session_state.dashboard_state
    time_since_change = t_sec - dash_state["active_tasks_last_change"]

    if current_active_str != dash_state["active_tasks_display"]:
        # State changed
        if time_since_change >= dash_state["stability_delay"]:
            # Enough time passed, allow change
            dash_state["active_tasks_display"] = current_active_str
            dash_state["active_tasks_last_change"] = t_sec
    # else: use cached display value for stability

    display_active_str = dash_state["active_tasks_display"]

    # Dashboard metrics row - FIXED HEIGHT (min-height prevents shifting)
    dash_cols = st.columns([1.5, 1, 1, 1.2])

    with dash_cols[0]:
        # Active tasks indicator (status bar style) - NO EMOJIS
        if display_active_str:
            st.markdown(f"""
            <div style='background: linear-gradient(90deg, #1e3a8a 0%, #3b82f6 100%);
                        padding: 14px; border-radius: 8px; text-align: center; min-height: 62px; display: flex; flex-direction: column; justify-content: center;'>
                <div style='color: #60a5fa; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;'>ACTIVE TASKS</div>
                <div style='color: white; font-size: 14px; font-weight: 700; margin-top: 4px;'>{display_active_str}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style='background: #1f2937; padding: 14px; border-radius: 8px; text-align: center; min-height: 62px; display: flex; flex-direction: column; justify-content: center;'>
                <div style='color: #9ca3af; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;'>STANDBY</div>
                <div style='color: #6b7280; font-size: 14px; font-weight: 700; margin-top: 4px;'>No Active Tasks</div>
            </div>
            """, unsafe_allow_html=True)

    with dash_cols[1]:
        # Safety alerts - NO EMOJIS
        alert_color = "#dc2626" if critical_alerts > 0 else ("#f59e0b" if warning_alerts > 0 else "#10b981")
        alert_text = f"{critical_alerts + warning_alerts} Active" if (critical_alerts + warning_alerts) > 0 else "All Clear"
        st.markdown(f"""
        <div style='background: #1f2937; padding: 14px; border-radius: 8px; text-align: center; min-height: 62px; display: flex; flex-direction: column; justify-content: center;'>
            <div style='color: #9ca3af; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;'>SAFETY</div>
            <div style='color: {alert_color}; font-size: 14px; font-weight: 700; margin-top: 4px;'>{alert_text}</div>
        </div>
        """, unsafe_allow_html=True)

    with dash_cols[2]:
        # Detections count - NO EMOJIS
        st.markdown(f"""
        <div style='background: #1f2937; padding: 14px; border-radius: 8px; text-align: center; min-height: 62px; display: flex; flex-direction: column; justify-content: center;'>
            <div style='color: #9ca3af; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;'>AIRSIDE AIRCRAFT RELEVANT</div>
            <div style='color: white; font-size: 14px; font-weight: 700; margin-top: 4px;'>{person_count}P · {vehicle_count}V</div>
        </div>
        """, unsafe_allow_html=True)

    with dash_cols[3]:
        # Sequence progress - NO EMOJIS
        progress_color = "#10b981" if seq_progress == 100 else ("#3b82f6" if seq_progress > 0 else "#6b7280")
        st.markdown(f"""
        <div style='background: #1f2937; padding: 14px; border-radius: 8px; text-align: center; min-height: 62px; display: flex; flex-direction: column; justify-content: center;'>
            <div style='color: #9ca3af; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;'>PROGRESS</div>
            <div style='color: {progress_color}; font-size: 14px; font-weight: 700; margin-top: 4px;'>{seq_progress}% Complete</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")  # Separator between dashboard and video

    # Now render the live video with overlay
    title = f"Flight LX-123 (Gate A12) · Run {st.session_state.run_id} · t={t_sec:.0f}s · {frame_path.name}"
    overlay_rois = rois if show_rois else None

    dets_df_view = dets_df
    if dets_df_view is not None and not dets_df_view.empty and "cls_name" in dets_df_view.columns:
        dets_df_view = dets_df_view[dets_df_view["cls_name"].isin(overlay_classes)].copy()

    overlay = draw_overlay(img, dets_df_view, title=title, rois=overlay_rois, asset_roles=st.session_state.asset_roles)
    st.image(overlay, use_container_width=True)

with right:
    st.markdown("## Dispatcher Console")
    st.markdown("**Turnaround**")
    st.caption(f"Flight: **LX-123** (Gate A12) · Run: `{st.session_state.run_id}`")

    # Export functionality
    st.markdown("### Export Results")
    col_exp1, col_exp2 = st.columns(2)
    with col_exp1:
        if st.button("📊 Export JSON", use_container_width=True):
            import json
            from datetime import datetime

            export_data = {
                "run_id": st.session_state.run_id,
                "timestamp": datetime.now().isoformat(),
                "current_time_sec": float(t_sec),
                "alerts": [
                    {
                        "alert_id": a.alert_id,
                        "severity": a.severity,
                        "rule_id": a.rule_id,
                        "message": a.message,
                        "first_seen": float(a.first_seen),
                        "last_seen": float(a.last_seen),
                        "status": a.status
                    }
                    for a in st.session_state.alerts.values()
                ],
                "task_history": {
                    k: {
                        "status": v.get("status"),
                        "since": float(v.get("since")) if v.get("since") is not None else None,
                        "last_seen": float(v.get("last_seen")) if v.get("last_seen") is not None else None
                    }
                    for k, v in st.session_state.task_hist.items()
                },
                "sequence_state": {
                    "current_idx": st.session_state.seq_state.current_idx,
                    "started_at": {k: float(v) for k, v in st.session_state.seq_state.started_at.items()},
                    "done_at": {k: float(v) for k, v in st.session_state.seq_state.done_at.items()}
                },
                "asset_roles": {str(k): v for k, v in st.session_state.asset_roles.items()}
            }

            json_str = json.dumps(export_data, indent=2)
            st.download_button(
                label="⬇️ Download JSON",
                data=json_str,
                file_name=f"turnaround_{st.session_state.run_id}_{int(t_sec)}s.json",
                mime="application/json"
            )

    with col_exp2:
        if st.button("📄 Export CSV", use_container_width=True):
            if alerts_df is not None and not alerts_df.empty:
                csv = alerts_df.to_csv(index=False)
                st.download_button(
                    label="⬇️ Download CSV",
                    data=csv,
                    file_name=f"alerts_{st.session_state.run_id}_{int(t_sec)}s.csv",
                    mime="text/csv"
                )
            else:
                st.info("No alerts to export")

    st.markdown("### Asset tagging (human-in-the-loop)")
    st.caption("Tag detected vehicles once → tasks become realistic.")
    render_asset_tagging(dets_df)

# ----------------------------
# Tabs under frame
# ----------------------------
st.markdown("---")
ops_slot = st.empty()

with ops_slot.container():
    tabs = st.tabs(["Turnaround Operations", "Alerts", "Event log", "Timeline"])


    def _seq_step_status(step_key: str, requires: List[str], now_t: float, deadline: Optional[float]) -> Tuple[str, str]:
        th = st.session_state.task_hist.get(step_key, {})
        status = str(th.get("status", "NOT_STARTED"))

        prereq_done = all(
            (k in st.session_state.seq_state.done_at) or (str(st.session_state.task_hist.get(k, {}).get("status")) == "DONE")
            for k in requires
        )
        active = (status == "ACTIVE")
        done = (step_key in st.session_state.seq_state.done_at) or (status == "DONE")

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
        if label == "DONE":
            return "DONE", "#1f7a3a"
        if label == "ACTIVE":
            return "ACTIVE", "#1f7a3a"
        if label == "BLOCKED":
            return "BLOCKED", "#7a1f1f"
        if label == "OVERDUE":
            return "OVERDUE", "#b34b00"
        return "WAITING", "#3b3b3b"


    with tabs[0]:
        st.markdown("### Sequence State Machine")
        st.caption("GPU → Fuel → Baggage → Pushback")

        st.session_state.seq_done_sensitivity = st.slider(
            "DONE sensitivity",
            min_value=1.0,
            max_value=20.0,
            value=float(st.session_state.seq_done_sensitivity),
            step=0.5,
        )

        done_count = 0
        for s in seq:
            th = st.session_state.task_hist.get(s.key, {})
            if (s.key in st.session_state.seq_state.done_at) or (str(th.get("status")) == "DONE"):
                done_count += 1
        st.progress(done_count / max(1, len(seq)))
        st.caption(f"{done_count}/{len(seq)} steps done")

        for step in seq:
            requires = step.requires_done
            status_label, hint = _seq_step_status(step.key, requires, t_sec, step.deadline_sec)
            display, color = _pill(status_label)

            left2, mid2, right2 = st.columns([1.4, 1.2, 2.2], gap="small")
            with left2:
                st.markdown(f"**{step.title}**")
                st.caption("requires: " + (", ".join(requires) if requires else "—"))

            with mid2:
                st.markdown(
                    f"<span style='display:inline-block;padding:0.20rem 0.55rem;border-radius:0.65rem;"
                    f"background:{color};color:white;font-weight:600;font-size:0.80rem'>{display}</span>",
                    unsafe_allow_html=True,
                )

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
                with st.expander("Task evidence", expanded=False):
                    th = st.session_state.task_hist.get(step.key, {})
                    st.code(str(th), language="json")


    with tabs[1]:
        st.markdown("### Alerts")
        st.caption("Unified alert feed: safety + sequence (order/deadline)")

        if alerts_df is None or alerts_df.empty:
            st.info("No active alerts.")
        else:
            st.dataframe(alerts_df, width="stretch")


    with tabs[2]:
        st.markdown("### Event log")
        if not st.session_state.event_log:
            st.info("No events yet.")
        else:
            rows = [{"t": e.t_sec, "level": e.level, "msg": e.msg} for e in st.session_state.event_log[-120:]]
            df = pd.DataFrame(rows).sort_values("t", ascending=False)
            st.dataframe(df, width="stretch")


    with tabs[3]:
        st.markdown("### Timeline")
        rows = []
        for k, h in st.session_state.task_hist.items():
            rows.append({"task": k, "status": h.get("status"), "since": h.get("since"), "last_seen": h.get("last_seen")})
        df = pd.DataFrame(rows)
        if df.empty:
            st.info("No task history.")
        else:
            st.dataframe(df.sort_values(["status", "task"]), width="stretch")


# ----------------------------
# Playback loop
# ----------------------------
if st.session_state.playback_running:
    time.sleep(1.0 / max(1, int(playback_fps)))
    nxt = idx + 1

    if nxt >= len(frames):
        if loop_playback:
            nxt = 0

            # loop restart: reset ONLY time-dependent state (KEEP tracker + asset_roles)
            st.session_state.task_hist = {}
            st.session_state.task_counters = {}
            st.session_state.alerts = {}
            st.session_state.seq_state = SequenceState()

            _log("info", 0.0, "Loop restart: state reset (tracker+tagging kept)")
        else:
            nxt = len(frames) - 1
            st.session_state.playback_running = False

    st.session_state.playback_idx = nxt
    st.rerun()
