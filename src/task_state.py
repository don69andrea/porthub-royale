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
    det rows: [x1,y1,x2,y2,conf,cls,track_id]
    """
    if det is None or len(det) == 0:
        return False

    for row in det:
        x1, y1, x2, y2, conf, cls_id, tid = row.tolist()
        name = cls_names.get(int(cls_id), str(int(cls_id))).lower()
        if name in class_set:
            if roi is None:
                return True
            if _center_in_roi(x1, y1, x2, y2, roi):
                return True
    return False


def infer_task_states(
    frames_det: List[Dict[str, Any]],
    *,
    aircraft_roi: Optional[Tuple[int, int, int, int]] = None,
    engine_roi: Optional[Tuple[int, int, int, int]] = None,
    mapping: Optional[Dict[str, List[str]]] = None,
    min_active_seconds: int = 15,
    done_grace_seconds: int = 10,
) -> List[TaskState]:
    """
    Converts detections into human-readable turnaround task states.
    Works best with 1fps input (your case).
    """
    if mapping is None:
        mapping = {}

    def _set(key: str, defaults: List[str]) -> Set[str]:
        vals = mapping.get(key, defaults)
        return {v.lower().strip() for v in vals}

    # You can tune these names depending on what YOLO actually outputs.
    person_classes = _set("person_classes", ["person"])
    gpu_classes = _set("gpu_classes", ["gpu", "groundpower", "truck", "car"])
    fuel_classes = _set("fuel_classes", ["fueltruck", "truck"])
    stairs_classes = _set("stairs_classes", ["stair", "stairs"])
    belt_classes = _set("belt_classes", ["beltloader", "belt loader"])
    baggage_cart_classes = _set("baggage_cart_classes", ["baggagecart", "baggage cart", "cart", "trolley", "trolly"])
    pushback_classes = _set("pushback_classes", ["pushback", "tug", "tractor"])

    # Define tasks as "presence signals"
    task_defs = [
        ("Safety: Engine zone clear", person_classes, engine_roi, "person in engine zone"),
        ("GPU connected", gpu_classes, aircraft_roi, "gpu near aircraft"),
        ("Fueling", fuel_classes, aircraft_roi, "fuel truck near aircraft"),
        ("Stairs positioned", stairs_classes, aircraft_roi, "stairs near aircraft"),
        ("Baggage unloading/loading", baggage_cart_classes | belt_classes, aircraft_roi, "belt/cart near aircraft"),
        ("Pushback", pushback_classes, aircraft_roi, "tug/pushback near aircraft"),
    ]

    # Track when each task signal is seen
    first_seen: Dict[str, Optional[float]] = {t[0]: None for t in task_defs}
    last_seen: Dict[str, Optional[float]] = {t[0]: None for t in task_defs}
    ever_seen: Dict[str, bool] = {t[0]: False for t in task_defs}

    for f in frames_det:
        t = float(f["t_sec"])
        det = f["det"]
        cls_names = f["cls_names"]

        for task, class_set, roi, ev in task_defs:
            present = _classes_present_in_frame(det, cls_names, class_set, roi=roi)
            if present:
                ever_seen[task] = True
                if first_seen[task] is None:
                    first_seen[task] = t
                last_seen[task] = t

    # Determine status based on last frame time
    last_t = float(frames_det[-1]["t_sec"])

    out: List[TaskState] = []
    for task, class_set, roi, ev in task_defs:
        fs = first_seen[task]
        ls = last_seen[task]

        if not ever_seen[task]:
            out.append(TaskState(task=task, status="NOT_STARTED", since_sec=None, last_seen_sec=None, evidence=ev))
            continue

        # ACTIVE if seen recently
        if ls is not None and (last_t - ls) <= done_grace_seconds:
            out.append(TaskState(task=task, status="ACTIVE", since_sec=fs, last_seen_sec=ls, evidence=ev))
            continue

        # DONE if it lasted long enough and isn't active anymore
        if fs is not None and ls is not None and (ls - fs) >= min_active_seconds:
            out.append(TaskState(task=task, status="DONE", since_sec=fs, last_seen_sec=ls, evidence=ev))
        else:
            # Seen only briefly → treat as NOT_STARTED (noise)
            out.append(TaskState(task=task, status="NOT_STARTED", since_sec=None, last_seen_sec=None, evidence=ev))

    return out
