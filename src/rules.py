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
    engine_violation_cooldown: float = 3.0,
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

    last_engine_alert_t: Optional[float] = None

    for f in frames_det:
        t_sec = float(f.get("t_sec", 0.0))
        det = f.get("det", None)
        cls_names: Dict[int, str] = f.get("cls_names", {})

        if det is None or len(det) == 0:
            # decay streak
            person_in_aircraft_streak = max(0, person_in_aircraft_streak - 1)
            continue

        det = np.asarray(det)
        if det.ndim != 2 or det.shape[1] < 6:
            continue

        xyxy = det[:, 0:4]
        cls_ids = det[:, 5].astype(int)
        cls = np.array([cls_names.get(int(i), str(int(i))) for i in cls_ids])

        # GPU seen
        if gpu_first_t is None and any(c in gpu_classes for c in cls.tolist()):
            gpu_first_t = t_sec
            alerts.append(Alert(t_sec=t_sec, severity="info", rule_id="gpu_seen", message="GPU detected (first sighting)."))

        # People logic
        is_person = np.isin(cls, np.array(person_classes))
        if is_person.any():
            persons_xyxy = xyxy[is_person]

            # Engine ROI safety
            if engine_roi is not None:
                in_engine = _in_roi(persons_xyxy, engine_roi)
                n_engine = int(in_engine.sum())
                if n_engine > max_people_in_engine_roi:
                    # cooldown
                    if last_engine_alert_t is None or (t_sec - last_engine_alert_t) >= float(engine_violation_cooldown):
                        last_engine_alert_t = t_sec
                        alerts.append(
                            Alert(
                                t_sec=t_sec,
                                severity="critical",
                                rule_id="engine_zone_people",
                                message=f"Too many people in engine zone ({n_engine}).",
                            )
                        )

            # Aircraft ROI -> infer chocks (placeholder)
            if aircraft_roi is not None:
                in_aircraft = _in_roi(persons_xyxy, aircraft_roi)
                if bool(in_aircraft.any()):
                    person_in_aircraft_streak += 1
                else:
                    person_in_aircraft_streak = max(0, person_in_aircraft_streak - 1)

                if (not chocks_inferred) and person_in_aircraft_streak >= 8:
                    chocks_inferred = True
                    alerts.append(
                        Alert(
                            t_sec=t_sec,
                            severity="warning",
                            rule_id="chocks_inferred",
                            message="Chocks inferred (person stayed near aircraft for a while).",
                        )
                    )

    # If GPU never observed
    if gpu_first_t is None:
        alerts.append(Alert(t_sec=0.0, severity="warning", rule_id="gpu_missing", message="GPU not observed in the sequence."))

    return alerts
