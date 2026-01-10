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
    """Track fingerdock position over time."""

    status: str = "NOT_CONNECTED"  # NOT_CONNECTED | OPERATING | CONNECTED
    first_detected: Optional[float] = None
    connected_at: Optional[float] = None

    # Position tracking for movement detection
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
    Detect fingerdock position and status.

    Detection heuristic (without custom model):
    - Check for large objects (truck/bus class) in fingerdock ROI
    - Track position changes to detect movement
    - Assume connected after stationary for 10+ seconds

    Returns:
        "NOT_CONNECTED" - Fingerdock not detected or not at aircraft
        "OPERATING" - Fingerdock moving/extending
        "CONNECTED" - Fingerdock stationary at aircraft (cover deployed)
    """
    if roi_fingerdock is None or dets_df is None or dets_df.empty:
        return state.status

    if "cls_name" not in dets_df.columns:
        return state.status

    # Look for large objects in fingerdock ROI
    # Fingerdock might be detected as truck, bus, or even train
    candidates = dets_df[dets_df["cls_name"].isin(["truck", "bus", "train"])]

    found_dock = False
    current_position = None

    for _, row in candidates.iterrows():
        bbox = tuple(row["bbox_xyxy"])
        if _in_roi(bbox, roi_fingerdock):
            found_dock = True
            x1, y1, x2, y2 = bbox
            current_position = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            break

    if not found_dock:
        # No dock detected - reset or keep last known state
        if state.status == "CONNECTED":
            # Keep connected status even if temporarily not detected
            return state.status
        else:
            state.status = "NOT_CONNECTED"
            return state.status

    # First detection
    if state.first_detected is None:
        state.first_detected = t_sec
        state.last_position = current_position
        state.last_position_time = t_sec
        state.status = "NOT_CONNECTED"
        return state.status

    # Check for movement
    if state.last_position is not None and current_position is not None:
        dx = abs(current_position[0] - state.last_position[0])
        dy = abs(current_position[1] - state.last_position[1])
        distance = (dx**2 + dy**2)**0.5

        # Movement threshold (pixels)
        movement_threshold = 15.0

        if distance > movement_threshold:
            # Fingerdock is moving
            state.is_moving = True
            state.status = "OPERATING"
            state.last_position = current_position
            state.last_position_time = t_sec
        else:
            # Stationary
            if state.is_moving and state.last_position_time is not None:
                # Was moving, now stopped
                time_stationary = t_sec - state.last_position_time

                if time_stationary >= 10.0:
                    # Stationary for 10+ seconds -> Connected
                    state.status = "CONNECTED"
                    state.is_moving = False
                    if state.connected_at is None:
                        state.connected_at = t_sec
                else:
                    # Still in OPERATING phase (recently stopped)
                    state.status = "OPERATING"
            elif state.status == "NOT_CONNECTED":
                # Never moved, just appeared stationary
                # Might already be connected from previous session
                time_since_first = t_sec - state.first_detected

                if time_since_first >= 15.0:
                    # Visible and stationary for 15+ seconds -> assume connected
                    state.status = "CONNECTED"
                    if state.connected_at is None:
                        state.connected_at = t_sec

    return state.status
