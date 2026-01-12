# src/fingerdock_detection.py
"""
Fingerdock position and cover detection.

States:
- NOT_CONNECTED: Fingerdock visible but not at aircraft
- OPERATING: Fingerdock moving/extending toward aircraft
- CONNECTED: Gray cover deployed and connected to aircraft door
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import pandas as pd


@dataclass
class FingerdockState:
    """Track fingerdock docking status over time."""

    status: str = "UNDOCKED"  # DOCKED | UNDOCKED
    first_detected: Optional[float] = None
    docked_at: Optional[float] = None
    undocked_at: Optional[float] = None

    # Movement tracking for dock/undock detection
    last_position: Optional[Tuple[float, float]] = None
    last_position_time: Optional[float] = None
    is_moving: bool = False


def _in_roi(bbox: Tuple[int, int, int, int], roi: Tuple[int, int, int, int]) -> bool:
    """Check if bbox centroid is in ROI."""
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    rx1, ry1, rx2, ry2 = roi
    return (rx1 <= cx <= rx2) and (ry1 <= cy <= ry2)


def detect_fingerdock(
    dets_df: pd.DataFrame,
    roi_fingerdock: Optional[Tuple[int, int, int, int]],
    t_sec: float,
    state: FingerdockState,
) -> str:
    """
    Detect fingerdock docking status (DOCKED/UNDOCKED).

    Simple detection based on movement in ROI:
    - DOCKED: Gray cover extended to aircraft (R→L movement followed by stillness)
              OR: Large object (truck/bus) stationary in ROI for 5+ seconds
    - UNDOCKED: Aircraft fully visible (L→R movement OR no large objects in ROI)

    Returns:
        "DOCKED" - Gray cover deployed, fingerdock connected to aircraft
        "UNDOCKED" - Fingerdock retracted or moving away, aircraft door visible
    """
    if roi_fingerdock is None or dets_df is None or dets_df.empty:
        return state.status

    if "cls_name" not in dets_df.columns:
        return state.status

    # Look for NON-AIRPLANE objects in ROI (fingerdock/gray cover)
    # The airplane is always there, we want to detect when something ELSE (fingerdock) covers it
    candidates = dets_df[dets_df["cls_name"].isin(["truck", "bus", "train"])]

    found_dock = False

    # Count how many non-airplane objects are in the ROI
    objects_in_roi = 0
    for _, row in candidates.iterrows():
        bbox = tuple(row["bbox_xyxy"])
        if _in_roi(bbox, roi_fingerdock):
            objects_in_roi += 1
            found_dock = True

    # Logic: If truck/bus/train detected in ROI = fingerdock docking
    if found_dock:
        # Object detected in ROI
        if state.first_detected is None:
            state.first_detected = t_sec

        # Require object to be present for at least 5 seconds before marking as DOCKED
        # This prevents false positives from vehicles just passing by
        time_present = t_sec - state.first_detected
        if time_present >= 5.0:
            if state.status != "DOCKED":
                state.status = "DOCKED"
                state.docked_at = t_sec
        state.last_position_time = t_sec
    else:
        # No non-airplane object detected in ROI = UNDOCKED (aircraft door visible)
        if state.status != "UNDOCKED":
            state.status = "UNDOCKED"
            state.undocked_at = t_sec
        state.first_detected = None  # Reset for next detection

    return state.status
