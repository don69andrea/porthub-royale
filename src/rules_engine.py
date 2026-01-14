# src/rules_engine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd


# ----------------------------
# Task catalog
# ----------------------------
TASKS: List[Dict[str, Any]] = [
    {"key": "passenger_unboarding", "title": "Passenger Unboarding", "role": "PERSON", "roi": "passenger_flow_window"},
    {"key": "passenger_boarding", "title": "Passenger Boarding", "role": "PERSON", "roi": "passenger_flow_window"},
    {"key": "wheel_chocks", "title": "Wheel Chocks Placed", "role": "WHEEL_CHOCKS_CREW", "roi": "wheel_chocks"},
    {"key": "fueling", "title": "Fueling", "role": "FUEL_TRUCK", "roi": "fuel"},
    {"key": "gpu", "title": "GPU connected", "role": ["GPU_TRUCK", "GPU_OPERATOR"], "roi": "nose"},  # Accept truck OR operator
    {"key": "baggage_front", "title": "Baggage Front Door", "role": "BELT_LOADER", "roi": "baggage_front"},
    {"key": "baggage_rear", "title": "Baggage Rear Door", "role": "BELT_LOADER", "roi": "baggage_rear"},
    {"key": "pushback", "title": "Pushback", "role": "PUSHBACK_TUG", "roi": "pushback"},
    # Safety tasks: ENGINE_SAFETY_CREW places pylons at engines
    {"key": "safety_engine_right_clear", "title": "Safety: Right Engine Pylon", "role": "ENGINE_SAFETY_CREW", "roi": "engine_right"},
    {"key": "safety_engine_left_clear", "title": "Safety: Left Engine Pylon", "role": "ENGINE_SAFETY_CREW", "roi": "engine_left"},
    {"key": "safety_pushback_clear", "title": "Safety: Pushback area clear", "role": "PERSON", "roi": "pushback"},
    {"key": "safety_airside_presence", "title": "Safety: Airside presence", "role": "PERSON", "roi": "aircraft"},
]


# ----------------------------
# Alerts
# ----------------------------
@dataclass
class AlertItem:
    alert_id: str
    severity: str          # INFO | WARNING | CRITICAL
    rule_id: str
    message: str
    first_seen: float
    last_seen: float
    status: str = "OPEN"   # OPEN | ACK | CLOSED


def _in_roi(bbox_xyxy: Tuple[int, int, int, int], roi: Tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = bbox_xyxy
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    rx1, ry1, rx2, ry2 = roi
    return (rx1 <= cx <= rx2) and (ry1 <= cy <= ry2)


def _ensure_task(task_hist: Dict[str, Dict[str, Any]], key: str) -> Dict[str, Any]:
    return task_hist.setdefault(key, {"status": "NOT_STARTED", "since": None, "last_seen": None})


def _set_status(task_hist: Dict[str, Dict[str, Any]], key: str, status: str, t_sec: float, log: Callable[[str, float, str], None]):
    h = _ensure_task(task_hist, key)
    prev = str(h.get("status", "NOT_STARTED"))
    if prev != status:
        h["status"] = status
        h["since"] = float(t_sec)
        log("task", t_sec, f"{key} => {status}")
    h["last_seen"] = float(t_sec)


def _upsert_alert(
    alerts: Dict[str, AlertItem],
    *,
    alert_id: str,
    severity: str,
    rule_id: str,
    message: str,
    now_t: float,
    log: Callable[[str, float, str], None],
):
    a = alerts.get(alert_id)
    if a is None:
        alerts[alert_id] = AlertItem(
            alert_id=alert_id,
            severity=severity,
            rule_id=rule_id,
            message=message,
            first_seen=float(now_t),
            last_seen=float(now_t),
        )
        log("alert", now_t, f"[{severity}] {message}")
    else:
        a.last_seen = float(now_t)
        # escalation
        sev_rank = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
        if sev_rank.get(severity, 0) > sev_rank.get(a.severity, 0):
            a.severity = severity
        a.message = message
        alerts[alert_id] = a


def _close_alert(alerts: Dict[str, AlertItem], *, alert_id: str, now_t: float):
    a = alerts.get(alert_id)
    if a is None:
        return
    a.last_seen = float(now_t)
    a.status = "CLOSED"
    alerts[alert_id] = a


# ----------------------------
# Task evaluation
# ----------------------------
def eval_tasks(
    dets_df: pd.DataFrame,
    rois: Dict[str, Optional[Tuple[int, int, int, int]]],
    t_sec: float,
    asset_roles: Dict[int, str],
    task_hist: Dict[str, Dict[str, Any]],
    task_counters: Dict[str, Dict[str, Any]],
    log: Callable[[str, float, str], None],
) -> None:
    def any_role_in_roi(role, roi_key: str, exclude_roles: List[str] = None) -> bool:
        """
        Check if role(s) are present in ROI.
        role can be a string or a list of strings (any match returns True).
        """
        roi = rois.get(roi_key)
        if roi is None or dets_df is None or dets_df.empty:
            return False

        # Support multiple roles (e.g., ["GPU_TRUCK", "GPU_OPERATOR"])
        roles_to_check = [role] if isinstance(role, str) else role

        for r in roles_to_check:
            # People not tagged: use cls_name (but exclude authorized roles)
            if r == "PERSON":
                if "cls_name" not in dets_df.columns:
                    continue
                people = dets_df[dets_df["cls_name"] == "person"]

                # Exclude persons with authorized roles (e.g., ENGINE_SAFETY_CREW in engine zone)
                if exclude_roles:
                    excluded_track_ids = [tid for tid, role_name in asset_roles.items() if role_name in exclude_roles]
                    if excluded_track_ids:
                        people = people[~people["track_id"].isin(excluded_track_ids)]

                for _, rr in people.iterrows():
                    if _in_roi(tuple(rr["bbox_xyxy"]), roi):
                        return True

            else:
                # Tagged role (vehicle or person)
                tagged_ids = [int(tid) for tid, role_name in asset_roles.items() if role_name == r]

                # DEBUG: Log for GPU role detection
                if r in ["GPU_TRUCK", "GPU_OPERATOR"]:
                    log("debug", t_sec, f"[GPU DEBUG] Checking role={r}, tagged_ids={tagged_ids}, roi_key={roi_key}, roi={roi}")

                if not tagged_ids:
                    if r in ["GPU_TRUCK", "GPU_OPERATOR"]:
                        log("debug", t_sec, f"[GPU DEBUG] No tagged IDs found for role {r}")
                    continue

                # Ensure track_id column is int for comparison
                if "track_id" not in dets_df.columns:
                    if r in ["GPU_TRUCK", "GPU_OPERATOR"]:
                        log("debug", t_sec, f"[GPU DEBUG] No track_id column in dets_df")
                    continue

                sub = dets_df[dets_df["track_id"].isin(tagged_ids)].copy()

                # DEBUG: Log current detections
                if r in ["GPU_TRUCK", "GPU_OPERATOR"]:
                    all_track_ids = dets_df["track_id"].tolist() if "track_id" in dets_df.columns else []
                    log("debug", t_sec, f"[GPU DEBUG] All track_ids in frame: {all_track_ids}")
                    log("debug", t_sec, f"[GPU DEBUG] Matched detections: {len(sub)} (looking for track_ids={tagged_ids})")

                if sub.empty:
                    if r in ["GPU_TRUCK", "GPU_OPERATOR"]:
                        log("debug", t_sec, f"[GPU DEBUG] No detections found with track_ids={tagged_ids} in current frame")
                    continue

                for _, rr in sub.iterrows():
                    bbox = tuple(rr["bbox_xyxy"])
                    in_roi_result = _in_roi(bbox, roi)

                    # DEBUG: Log ROI check
                    if r in ["GPU_TRUCK", "GPU_OPERATOR"]:
                        log("debug", t_sec, f"[GPU DEBUG] Detection track_id={rr['track_id']}, bbox={bbox}, in_roi={in_roi_result}")

                    if in_roi_result:
                        return True

        return False

    # Evaluate each task state
    for t in TASKS:
        key = t["key"]
        role = t["role"]
        roi_key = t["roi"]

        seen = any_role_in_roi(role, roi_key)
        h = _ensure_task(task_hist, key)

        prev_status = str(h.get("status", "NOT_STARTED"))
        if seen:
            # ACTIVE when detected in ROI
            _set_status(task_hist, key, "ACTIVE", t_sec, log)
            task_counters.setdefault(key, {})["last_seen"] = float(t_sec)
        else:
            # decay to INACTIVE if previously active
            if prev_status == "ACTIVE":
                _set_status(task_hist, key, "INACTIVE", t_sec, log)
            else:
                # keep NOT_STARTED or DONE etc.
                h["last_seen"] = float(t_sec)


# ----------------------------
# Alerts compilation for UI
# ----------------------------
def compute_alerts_df(
    now_t: float,
    task_hist: Dict[str, Dict[str, Any]],
    alerts: Dict[str, AlertItem],
    log: Callable[[str, float, str], None],
) -> pd.DataFrame:
    # Safety: Right Engine Pylon - ENGINE_SAFETY_CREW placing pylon is GOOD!
    h_right = task_hist.get("safety_engine_right_clear", {})
    if h_right.get("status") == "ACTIVE":
        _upsert_alert(
            alerts,
            alert_id="engine_right_pylon_placed",
            severity="INFO",
            rule_id="safety_engine_right_clear",
            message="✓ Right engine safety pylon placed by crew.",
            now_t=now_t,
            log=log,
        )
    else:
        _close_alert(alerts, alert_id="engine_right_pylon_placed", now_t=now_t)

    # Safety: Left Engine Pylon - ENGINE_SAFETY_CREW placing pylon is GOOD!
    h_left = task_hist.get("safety_engine_left_clear", {})
    if h_left.get("status") == "ACTIVE":
        _upsert_alert(
            alerts,
            alert_id="engine_left_pylon_placed",
            severity="INFO",
            rule_id="safety_engine_left_clear",
            message="✓ Left engine safety pylon placed by crew.",
            now_t=now_t,
            log=log,
        )
    else:
        _close_alert(alerts, alert_id="engine_left_pylon_placed", now_t=now_t)

    # Safety: pushback area not clear
    # ACTIVE = person IS in pushback zone → WARNING!
    h = task_hist.get("safety_pushback_clear", {})
    if h.get("status") == "ACTIVE":
        _upsert_alert(
            alerts,
            alert_id="pushback_area_not_clear",
            severity="WARNING",
            rule_id="safety_pushback_clear",
            message="WARNING: Person detected in Pushback ROI! Area NOT clear.",
            now_t=now_t,
            log=log,
        )
    else:
        _close_alert(alerts, alert_id="pushback_area_not_clear", now_t=now_t)

    # Safety: generic airside presence (informational)
    # ACTIVE = person IS airside → INFO (expected during turnaround)
    h = task_hist.get("safety_airside_presence", {})
    if h.get("status") == "ACTIVE":
        _upsert_alert(
            alerts,
            alert_id="airside_person_present",
            severity="INFO",
            rule_id="safety_airside_presence",
            message="Person detected airside (within Aircraft ROI). Normal during turnaround.",
            now_t=now_t,
            log=log,
        )
    else:
        _close_alert(alerts, alert_id="airside_person_present", now_t=now_t)

    # include existing seq alerts already in `alerts` (created by update_sequence)
    rows: List[Dict[str, Any]] = []
    for a in alerts.values():
        if a.status != "OPEN":
            continue
        rows.append(
            {
                "t_first": a.first_seen,
                "t_last": a.last_seen,
                "severity": a.severity,
                "rule": a.rule_id,
                "message": a.message,
                "id": a.alert_id,
            }
        )

    if not rows:
        return pd.DataFrame(columns=["t_first", "t_last", "severity", "rule", "message", "id"])

    sev_order = {"CRITICAL": 2, "WARNING": 1, "INFO": 0}
    df = pd.DataFrame(rows)
    df["_sev"] = df["severity"].map(lambda s: sev_order.get(s, 0))
    df = df.sort_values(["_sev", "t_last"], ascending=[False, False]).drop(columns=["_sev"])
    return df
