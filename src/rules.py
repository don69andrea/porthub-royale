from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np


@dataclass
class Alert:
    t_sec: float
    severity: str  # "info" | "warning" | "critical"
    rule_id: str
    message: str


def _in_roi(xyxy: np.ndarray, roi: Tuple[int, int, int, int]) -> np.ndarray:
    """Return boolean mask: box center within ROI."""
    x1, y1, x2, y2 = xyxy[:, 0], xyxy[:, 1], xyxy[:, 2], xyxy[:, 3]
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    rx1, ry1, rx2, ry2 = roi
    return (cx >= rx1) & (cx <= rx2) & (cy >= ry1) & (cy <= ry2)


def evaluate_rules(
    frames_det: List[dict],
    *,
    aircraft_roi: Optional[Tuple[int, int, int, int]],
    engine_roi: Optional[Tuple[int, int, int, int]],
    person_classes: List[str],
    gpu_classes: List[str],
    max_people_in_engine_roi: int = 1,
    gpu_deadline_seconds: int = 120,
    require_chocks_before_gpu: bool = True,
) -> List[Alert]:
    """
    `frames_det` is a list of dicts:
      {t_sec, det (Nx7), cls_names (id->name)}
    """
    alerts: List[Alert] = []

    # Track whether GPU was ever observed; keep earliest time
    gpu_first_t: Optional[float] = None

    # Placeholder "chocks placed": inferred when a person stays in aircraft ROI long enough
    chocks_inferred = False
    person_in_aircraft_streak = 0

    for f in frames_det:
        t = float(f["t_sec"])
        det = f["det"]
        cls_names: Dict[int, str] = f["cls_names"]

        if det.size == 0:
            continue

        xyxy = det[:, 0:4]
        cls_ids = det[:, 5].astype(int)

        names = np.array([cls_names.get(int(i), str(int(i))) for i in cls_ids], dtype=object)

        # --- rule: people in engine ROI (safety)
        if engine_roi is not None:
            is_person = np.isin(names, person_classes)
            in_engine = _in_roi(xyxy[is_person], engine_roi) if np.any(is_person) else np.array([], dtype=bool)
            n_people_engine = int(in_engine.sum()) if in_engine.size else 0
            if n_people_engine > max_people_in_engine_roi:
                alerts.append(Alert(
                    t_sec=t,
                    severity="critical",
                    rule_id="safety.people_in_engine_zone",
                    message=f"{n_people_engine} persons in engine zone (limit {max_people_in_engine_roi})",
                ))

        # --- infer "chocks placed" (demo heuristic)
        if aircraft_roi is not None:
            is_person = np.isin(names, person_classes)
            if np.any(is_person):
                in_aircraft = _in_roi(xyxy[is_person], aircraft_roi)
                if bool(in_aircraft.any()):
                    person_in_aircraft_streak += 1
                else:
                    person_in_aircraft_streak = 0
            else:
                person_in_aircraft_streak = 0

            # if person is near aircraft for ~3 sampled frames -> infer chocks
            if person_in_aircraft_streak >= 3:
                chocks_inferred = True

        # --- rule: GPU appears within deadline (efficiency)
        is_gpu = np.isin(names, gpu_classes)
        if np.any(is_gpu):
            if gpu_first_t is None:
                gpu_first_t = t

    if gpu_first_t is None:
        alerts.append(Alert(
            t_sec=float(gpu_deadline_seconds),
            severity="warning",
            rule_id="eff.gpu_missing",
            message=f"GPU not detected within first {gpu_deadline_seconds}s (proxy classes: {gpu_classes})",
        ))
    elif gpu_first_t > gpu_deadline_seconds:
        alerts.append(Alert(
            t_sec=float(gpu_first_t),
            severity="warning",
            rule_id="eff.gpu_late",
            message=f"GPU first detected at {gpu_first_t:.1f}s (deadline {gpu_deadline_seconds}s)",
        ))

    if require_chocks_before_gpu and (gpu_first_t is not None) and not chocks_inferred:
        alerts.append(Alert(
            t_sec=float(gpu_first_t),
            severity="warning",
            rule_id="order.chocks_before_gpu",
            message="GPU detected but chocks not inferred before GPU (demo heuristic)",
        ))

    return alerts
