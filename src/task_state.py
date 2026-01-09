from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set, Any
import numpy as np


@dataclass
class TaskState:
    task: str
    status: str  # NOT_STARTED | ACTIVE | DONE
    since_sec: Optional[float] = None
    last_seen_sec: Optional[float] = None
    evidence: str = ""


def _center_in_roi(x1: float, y1: float, x2: float, y2: float, roi: Tuple[int, int, int, int]) -> bool:
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    rx1, ry1, rx2, ry2 = roi
    return (rx1 <= cx <= rx2) and (ry1 <= cy <= ry2)


def _classes_present_in_frame(
    det: np.ndarray,
    cls_names: Dict[int, str],
    class_set: Set[str],
    roi: Optional[Tuple[int, int, int, int]] = None,
) -> bool:
    """
    det rows: [x1,y1,x2,y2,conf,cls,track]
    """
    if det is None or len(det) == 0:
        return False
    det = np.asarray(det)
    if det.ndim != 2 or det.shape[1] < 6:
        return False

    for row in det:
        x1, y1, x2, y2 = row[0:4]
        cls_id = int(row[5])
        name = cls_names.get(cls_id, str(cls_id))
        if name not in class_set:
            continue
        if roi is not None:
            if not _center_in_roi(float(x1), float(y1), float(x2), float(y2), roi):
                continue
        return True
    return False


def update_task_state(
    state: TaskState,
    present: bool,
    t_sec: float,
    *,
    done_after_sec: float = 5.0,
) -> TaskState:
    if present:
        if state.status == "NOT_STARTED":
            state.status = "ACTIVE"
            state.since_sec = float(t_sec)
        state.last_seen_sec = float(t_sec)
    else:
        # if was active long enough, consider done
        if state.status == "ACTIVE" and state.since_sec is not None:
            if (float(t_sec) - float(state.since_sec)) >= float(done_after_sec):
                state.status = "DONE"
        state.last_seen_sec = float(t_sec)
    return state


def infer_tasks_from_frames(
    frames_det: List[dict],
    *,
    rois: Dict[str, Optional[Tuple[int, int, int, int]]],
    cls_sets: Dict[str, Set[str]],
    done_after_sec: float = 5.0,
) -> Dict[str, TaskState]:
    """
    frames_det entries:
      {t_sec, det (Nx7), cls_names}
    """
    states: Dict[str, TaskState] = {}

    for f in frames_det:
        t_sec = float(f.get("t_sec", 0.0))
        det = f.get("det", None)
        cls_names: Dict[int, str] = f.get("cls_names", {})

        for task, cls_set in cls_sets.items():
            roi = rois.get(task)
            stt = states.get(task, TaskState(task=task, status="NOT_STARTED"))
            present = _classes_present_in_frame(det, cls_names, cls_set, roi=roi)
            stt = update_task_state(stt, present, t_sec, done_after_sec=done_after_sec)
            states[task] = stt

    return states
