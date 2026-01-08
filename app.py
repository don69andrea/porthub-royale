# app.py
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

# ============================================================
# Page
# ============================================================
st.set_page_config(page_title="PortHub Royale — Dispatcher Desk", layout="wide")

st.markdown(
    """
<style>
.block-container { padding-top: 1rem; padding-bottom: 1rem; }
</style>
""",
    unsafe_allow_html=True,
)

# ============================================================
# Dispatcher State (single source of truth for UI + ops logic)
# ============================================================
@dataclass
class EventLogItem:
    t_sec: float
    level: str   # "info" | "warning" | "task"
    message: str


@dataclass
class TaskHist:
    status: str = "NOT_STARTED"  # NOT_STARTED | ACTIVE | INACTIVE | DONE
    since: Optional[float] = None
    last_seen: Optional[float] = None


@dataclass
class AlertItem:
    id: str
    severity: str  # INFO | WARNING | CRITICAL
    rule_id: str
    message: str
    first_seen: float
    last_seen: float
    status: str = "OPEN"  # OPEN | ACK | CLOSED


@dataclass
class DispatcherState:
    run_id: str = "run-0001"

    # playback
    playback_running: bool = False
    playback_idx: int = 0
    t_sec: float = 0.0

    # human-in-the-loop: track_id -> role key
    asset_roles: Dict[int, str] = field(default_factory=dict)

    # task evaluation
    task_hist: Dict[str, TaskHist] = field(default_factory=dict)

    # stability counters per task key
    # key -> {"on": int, "off": int, "last": Optional[bool]}
    task_counters: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # alerts store
    alerts: Dict[str, AlertItem] = field(default_factory=dict)

    # event log (newest first)
    event_log: List[EventLogItem] = field(default_factory=list)


def get_state() -> DispatcherState:
    if "ph_state" not in st.session_state:
        st.session_state.ph_state = DispatcherState()
    return st.session_state.ph_state


state = get_state()

# tracker is "heavy" / object-y; keep it separately (works great for Streamlit)
if "tracker" not in st.session_state:
    st.session_state.tracker = SimpleIoUTracker(iou_match=0.35, max_missed=10)


def _log(level: str, t_sec: float, msg: str):
    state.event_log.insert(0, EventLogItem(t_sec=float(t_sec), level=level, message=str(msg)))
    state.event_log = state.event_log[:250]


# ============================================================
# Helpers (ROI, stability)
# ============================================================
def _bbox_center(b: Tuple[float, float, float, float]):
    x1, y1, x2, y2 = b
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _in_roi(bbox_xyxy: Tuple[float, float, float, float], roi: Tuple[int, int, int, int]) -> bool:
    cx, cy = _bbox_center(bbox_xyxy)
    x1, y1, x2, y2 = roi
    return (x1 <= cx <= x2) and (y1 <= cy <= y2)


def _stable_bool(key: str, cond: bool, on_n: int = 3, off_n: int = 3) -> Optional[bool]:
    """
    Debounce booleans so tasks don't flicker. Returns:
      - True / False when stable
      - None when still unstable (keep prior state)
    """
    c = state.task_counters.setdefault(key, {"on": 0, "off": 0, "last": None})
    if cond:
        c["on"] += 1
        c["off"] = 0
    else:
        c["off"] += 1
        c["on"] = 0

    if cond and c["on"] >= on_n:
        if c["last"] is not True:
            c["last"] = True
        return True
    if (not cond) and c["off"] >= off_n:
        if c["last"] is not False:
            c["last"] = False
        return False

    return None


# ============================================================
# Asset tagging (human-in-the-loop)
# ============================================================
ROLE_OPTIONS = {
    "UNASSIGNED": "Unassigned",
    "GPU": "GPU (Ground Power Unit)",
    "BELT_LOADER": "Belt / Baggage",
    "PUSHBACK_TUG": "Tug / Pushback",
    "STAIRS": "Stairs (proxy)",
    "CATERING": "Catering (proxy)",
    "FUEL": "Fuel Truck (proxy)",
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
        st.info("No vehicles detected in current frame.")
        return

    veh = veh.sort_values(["track_id", "conf"], ascending=[True, False])

    for _, r in veh.iterrows():
        tid = int(r["track_id"])
        conf = float(r.get("conf", 0.0))
        cls_name = str(r.get("cls_name", "obj"))

        current = state.asset_roles.get(tid, "UNASSIGNED")

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
                state.asset_roles[tid] = new_role
                _log("info", state.t_sec, f"Asset tagged: id={tid} as {ROLE_OPTIONS.get(new_role, new_role)}")
                st.rerun()


# ============================================================
# Tasks (demo-friendly, ROI driven)
# ============================================================
TASKS = [
    {"key": "fueling", "title": "Fueling", "role": "FUEL", "roi": "fuel"},
    {"key": "gpu_connected", "title": "GPU connected", "role": "GPU", "roi": "gpu"},
    {"key": "baggage_loading", "title": "Baggage loading", "role": "BELT_LOADER", "roi": "belt"},
    {"key": "catering", "title": "Catering", "role": "CATERING", "roi": "catering"},
    {"key": "pushback_ready", "title": "Pushback ready", "role": "PUSHBACK_TUG", "roi": "pushback"},
    {"key": "safety_engine_clear", "title": "Safety: Engine zone clear", "role": "PERSON", "roi": "engine_clear"},
]


def eval_tasks(dets_df: pd.DataFrame, rois: Dict[str, Optional[Tuple[int, int, int, int]]], t_sec: float):
    def any_role_in_roi(role: str, roi_key: str) -> bool:
        roi = rois.get(roi_key)
        if roi is None or dets_df is None or dets_df.empty:
            return False

        # People are not tagged; use cls_name
        if role == "PERSON":
            if "cls_name" not in dets_df.columns:
                return False
            people = dets_df[dets_df["cls_name"] == "person"]
            for _, rr in people.iterrows():
                if _in_roi(tuple(rr["bbox_xyxy"]), roi):
                    return True
            return False

        tagged_ids = [tid for tid, r in state.asset_roles.items() if r == role]
        if not tagged_ids:
            return False

        sub = dets_df[dets_df["track_id"].isin(tagged_ids)]
        for _, rr in sub.iterrows():
            if _in_roi(tuple(rr["bbox_xyxy"]), roi):
                return True
        return False

    for t in TASKS:
        key = t["key"]
        title = t["title"]

        # engine_clear is inverted condition (clear = no person in engine ROI)
        if t["roi"] == "engine_clear":
            engine_roi = rois.get("engine")
            cond_clear = True
            if engine_roi is not None and dets_df is not None and not dets_df.empty and "cls_name" in dets_df.columns:
                people = dets_df[dets_df["cls_name"] == "person"]
                for _, rr in people.iterrows():
                    if _in_roi(tuple(rr["bbox_xyxy"]), engine_roi):
                        cond_clear = False
                        break
            desired = _stable_bool(key, cond_clear, on_n=3, off_n=3)
        else:
            cond = any_role_in_roi(t["role"], t["roi"])
            desired = _stable_bool(key, cond, on_n=3, off_n=4)

        hist = state.task_hist.get(key)
        if hist is None:
            hist = TaskHist()
            state.task_hist[key] = hist

        # keep last_seen whenever raw condition is true (even if unstable)
        if t["roi"] == "engine_clear":
            # if NOT clear => people in engine => update last_seen for safety logic via desired below
            pass
        else:
            if desired is True:
                hist.last_seen = t_sec

        if desired is None:
            continue

        if desired is True:
            if hist.status in ["NOT_STARTED", "INACTIVE"]:
                hist.status = "ACTIVE"
                if hist.since is None:
                    hist.since = t_sec
                _log("task", t_sec, f"{title} → ACTIVE")
            hist.last_seen = t_sec

        elif desired is False:
            if hist.status == "ACTIVE":
                hist.status = "INACTIVE"
                _log("task", t_sec, f"{title} → INACTIVE (lost)")


# ============================================================
# Alerts (simple, deduped)
# ============================================================
def _upsert_alert(alert_id: str, severity: str, rule_id: str, message: str, now_t: float):
    a = state.alerts.get(alert_id)
    if a is None:
        state.alerts[alert_id] = AlertItem(
            id=alert_id,
            severity=severity,
            rule_id=rule_id,
            message=message,
            first_seen=now_t,
            last_seen=now_t,
            status="OPEN",
        )
        _log("warning" if severity in ["WARNING", "CRITICAL"] else "info", now_t, f"{severity}: {message}")
    else:
        a.last_seen = now_t
        # allow severity to escalate
        sev_rank = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
        if sev_rank.get(severity, 0) > sev_rank.get(a.severity, 0):
            a.severity = severity
            a.message = message


def compute_alerts(now_t: float) -> pd.DataFrame:
    # Safety critical: engine zone not clear
    h = state.task_hist.get("safety_engine_clear", TaskHist())
    if h.status == "INACTIVE":
        _upsert_alert(
            alert_id="engine_zone_not_clear",
            severity="CRITICAL",
            rule_id="safety_engine_clear",
            message="Engine zone NOT clear (person detected in Engine ROI).",
            now_t=now_t,
        )

    # Example deadline warning: GPU should be active by 120s
    gpu = state.task_hist.get("gpu_connected", TaskHist())
    if now_t >= 120 and gpu.status != "ACTIVE":
        _upsert_alert(
            alert_id="gpu_deadline_missed",
            severity="WARNING",
            rule_id="gpu_deadline",
            message="GPU not connected by t=120s (deadline missed).",
            now_t=now_t,
        )

    # Compact dataframe for UI
    rows = []
    for a in state.alerts.values():
        if a.status != "OPEN":
            continue
        rows.append(
            {
                "t_first": a.first_seen,
                "t_last": a.last_seen,
                "severity": a.severity,
                "rule": a.rule_id,
                "message": a.message,
                "id": a.id,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["t_first", "t_last", "severity", "rule", "message", "id"])
    df = pd.DataFrame(rows).sort_values(["severity", "t_last"], ascending=[False, False])
    return df


# ============================================================
# Sidebar UI (Input / Playback / Detection / ROIs)
# ============================================================
with st.sidebar:
    st.header("Input")
    frames_folder = st.text_input("Frames folder", value="data/frames_1fps")

    st.header("Playback (Live view)")
    playback_fps = st.slider("Playback FPS", 1, 10, 1)
    loop_playback = st.checkbox("Loop", value=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Play", use_container_width=True if False else False):  # (kept harmless)
            state.playback_running = True
    with c2:
        if st.button("Pause"):
            state.playback_running = False
    with c3:
        if st.button("Reset"):
            state.playback_running = False
            state.playback_idx = 0
            state.t_sec = 0.0
            _log("info", state.t_sec, "Playback reset")

    st.header("Detection")
    demo_mode = st.checkbox("Demo Mode (no ML deps)", value=False)
    conf = st.slider("Confidence", 0.05, 0.95, 0.35, 0.01)
    iou = st.slider("IoU", 0.05, 0.95, 0.50, 0.01)
    weights = st.text_input("YOLO weights", value="yolov8n.pt")

    st.header("ROIs (x1,y1,x2,y2)")
    roi_gpu = st.text_input("gpu", value="260,250,620,520")
    roi_fuel = st.text_input("fuel", value="620,170,980,520")
    roi_belt = st.text_input("belt", value="250,320,520,650")
    roi_catering = st.text_input("catering", value="580,260,920,600")
    roi_pushback = st.text_input("pushback", value="420,120,760,420")
    roi_engine = st.text_input("engine", value="330,300,620,560")

# Build ROI dict
rois: Dict[str, Optional[Tuple[int, int, int, int]]] = {
    "gpu": parse_roi(roi_gpu),
    "fuel": parse_roi(roi_fuel),
    "belt": parse_roi(roi_belt),
    "catering": parse_roi(roi_catering),
    "pushback": parse_roi(roi_pushback),
    "engine": parse_roi(roi_engine),
}

# ============================================================
# Frames / Playback
# ============================================================
frames = list_frames(frames_folder)
if not frames:
    st.error(f"No frames found in: {frames_folder}")
    st.stop()

# keep idx in bounds
idx = max(0, min(int(state.playback_idx), len(frames) - 1))
state.playback_idx = idx

frame_path = frames[idx]
t_sec = float(idx)  # 1fps assumption
state.t_sec = t_sec

img = Image.open(frame_path).convert("RGB")

# ============================================================
# Detection + Tracking
# ============================================================
model = None
if not demo_mode:
    @st.cache_resource
    def _cached_model(w: str):
        return load_model(w)

    model = _cached_model(weights)

raw = demo_detections(img, t_sec) if demo_mode else yolo_detect(model, img, conf=conf, iou=iou)
tracked = st.session_state.tracker.update(raw)
dets_df = detections_df_from_tracked(tracked)

# ============================================================
# Layout
# ============================================================
st.title("PortHub Royale — Dispatcher Desk")

left, right = st.columns([3.6, 1.7], gap="large")

with left:
    st.markdown("## Live Camera Feed (frames playback)")
    title = f"Flight LX-123 (Gate A12) · Run {state.run_id} · t={t_sec:.0f}s · {frame_path.name}"
    overlay = draw_overlay(img, dets_df, title=title, rois=rois, asset_roles=state.asset_roles)
    st.image(overlay, width="stretch")

with right:
    st.markdown("## Dispatcher Console")
    st.markdown("**Turnaround**")
    st.caption(f"Flight: **LX-123** (Gate A12) · Run: `{state.run_id}`")

    st.markdown("### Asset tagging (human-in-the-loop)")
    st.caption("Tag detected vehicles once → tasks become realistic.")
    render_asset_tagging(dets_df)

# Hook tasks evaluation
eval_tasks(dets_df, rois, t_sec=t_sec)

# ============================================================
# Tabs under frame
# ============================================================
st.markdown("---")
tabs = st.tabs(["Turnaround Operations", "Alerts", "Event log", "Timeline"])

with tabs[0]:
    for t in TASKS:
        key = t["key"]
        title = t["title"]
        hist = state.task_hist.get(key, TaskHist())

        status = hist.status
        since = hist.since
        last_seen = hist.last_seen

        c1, c2 = st.columns([3, 1], gap="small")
        with c1:
            st.subheader(title)
            if last_seen is not None and since is not None:
                st.caption(f"since {since:.0f}s · last seen {max(0.0, t_sec - last_seen):.0f}s ago")
            else:
                st.caption("not seen yet")

        with c2:
            pill = "ACTIVE" if status == "ACTIVE" else ("INACTIVE" if status == "INACTIVE" else "NOT_STARTED")
            bg = "#1f7a3a" if pill == "ACTIVE" else ("#7a1f1f" if pill == "INACTIVE" else "#3b3f46")
            st.markdown(
                f"""
                <div style="display:flex;justify-content:flex-end;align-items:center;height:56px;">
                  <span style="background:{bg};padding:8px 12px;border-radius:999px;font-weight:700;font-size:12px;">
                    {pill}
                  </span>
                </div>
                """,
                unsafe_allow_html=True,
            )

with tabs[1]:
    alerts_df = compute_alerts(t_sec)
    if alerts_df is None or alerts_df.empty:
        st.success("No alerts in current window.")
    else:
        st.dataframe(alerts_df, width="stretch", height=280)

with tabs[2]:
    if state.event_log:
        for e in state.event_log[:80]:
            badge = "⚠️" if e.level == "warning" else ("✅" if e.level == "task" else "ℹ️")
            st.write(f"{badge} **t={e.t_sec:.1f}s** — {e.message}")
    else:
        st.info("No events yet.")

with tabs[3]:
    rows = []
    for t in TASKS:
        h = state.task_hist.get(t["key"], TaskHist())
        rows.append(
            {
                "task": t["title"],
                "status": h.status,
                "since_sec": h.since,
                "last_seen_sec": h.last_seen,
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", height=260)

# ============================================================
# Playback loop
# ============================================================
if state.playback_running:
    time.sleep(1.0 / max(1, int(playback_fps)))
    nxt = idx + 1
    if nxt >= len(frames):
        if loop_playback:
            nxt = 0
        else:
            nxt = len(frames) - 1
            state.playback_running = False
    state.playback_idx = nxt
    st.rerun()
