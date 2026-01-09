# src/rules_engine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd


# ----------------------------
# Task catalog
# ----------------------------
TASKS: List[Dict[str, str]] = [
    {"key": "fueling", "title": "Fueling", "role": "FUEL_TRUCK", "roi": "fuel"},
    {"key": "gpu", "title": "GPU connected", "role": "GPU_TRUCK", "roi": "nose"},
    {"key": "baggage", "title": "Baggage unloading/loading", "role": "BELT_LOADER", "roi": "belly"},
    {"key": "pushback", "title": "Pushback", "role": "PUSHBACK_TUG", "roi": "aircraft"},
    # Safety tasks (people not tagged; handled as alerts)
    {"key": "safety_engine_clear", "title": "Safety: Engine zone clear", "role": "PERSON", "roi": "engine"},
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
    def any_role_in_roi(role: str, roi_key: str) -> bool:
        roi = rois.get(roi_key)
        if roi is None or dets_df is None or dets_df.empty:
            return False

        # People not tagged: use cls_name
        if role == "PERSON":
            if "cls_name" not in dets_df.columns:
                return False
            people = dets_df[dets_df["cls_name"] == "person"]
            for _, rr in people.iterrows():
                if _in_roi(tuple(rr["bbox_xyxy"]), roi):
                    return True
            return False

        tagged_ids = [tid for tid, r in asset_roles.items() if r == role]
        if not tagged_ids:
            return False

        sub = dets_df[dets_df["track_id"].isin(tagged_ids)]
        if sub.empty:
            return False

        for _, rr in sub.iterrows():
            if _in_roi(tuple(rr["bbox_xyxy"]), roi):
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
    # Safety: engine zone not clear
    # ACTIVE = person IS in engine zone → DANGER!
    h = task_hist.get("safety_engine_clear", {})
    if h.get("status") == "ACTIVE":
        _upsert_alert(
            alerts,
            alert_id="engine_zone_not_clear",
            severity="CRITICAL",
            rule_id="safety_engine_clear",
            message="DANGER: Person detected in Engine ROI! Engine zone NOT clear.",
            now_t=now_t,
            log=log,
        )
    else:
        _close_alert(alerts, alert_id="engine_zone_not_clear", now_t=now_t)

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
