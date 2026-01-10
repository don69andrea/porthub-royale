# src/passenger_flow.py
"""
Passenger flow detection with movement direction analysis.
Distinguishes between:
- Deboarding (left-to-right movement)
- Boarding (right-to-left movement)
- Ground staff preparation (right-to-left before deboarding)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import pandas as pd


@dataclass
class PassengerFlowState:
    """Track passenger movement patterns over time."""

    # Track individual person positions for direction detection
    person_positions: Dict[int, List[Tuple[float, float, float]]] = field(default_factory=dict)
    # track_id -> [(x, y, t_sec), ...]

    # Flow statistics
    left_to_right_count: int = 0  # Deboarding
    right_to_left_count: int = 0  # Boarding / Ground staff

    # Last detection times
    last_deboarding_detection: Optional[float] = None
    last_boarding_detection: Optional[float] = None
    last_ground_staff_detection: Optional[float] = None

    # Task states
    deboarding_started: bool = False
    boarding_started: bool = False
    ground_staff_prep_done: bool = False

    # Timing thresholds
    deboarding_timeout: float = 50.0  # No movement for 50s -> DONE
    boarding_timeout: float = 10.0    # Movement for 10s -> START


def _get_centroid(bbox: Tuple[int, int, int, int]) -> Tuple[float, float]:
    """Get center point of bounding box."""
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _in_roi(bbox: Tuple[int, int, int, int], roi: Tuple[int, int, int, int]) -> bool:
    """Check if bbox centroid is in ROI."""
    cx, cy = _get_centroid(bbox)
    rx1, ry1, rx2, ry2 = roi
    return (rx1 <= cx <= rx2) and (ry1 <= cy <= ry2)


def _detect_movement_direction(positions: List[Tuple[float, float, float]], min_samples: int = 3) -> Optional[str]:
    """
    Detect movement direction from position history.

    Returns:
        "left_to_right" - Person moving right (deboarding)
        "right_to_left" - Person moving left (boarding/ground staff)
        None - Not enough data or stationary
    """
    if len(positions) < min_samples:
        return None

    # Take last N positions
    recent = positions[-min_samples:]

    # Calculate x-coordinate changes
    x_changes = []
    for i in range(1, len(recent)):
        x_prev, _, _ = recent[i-1]
        x_curr, _, _ = recent[i]
        x_changes.append(x_curr - x_prev)

    if not x_changes:
        return None

    # Average change
    avg_change = sum(x_changes) / len(x_changes)

    # Threshold for significant movement (adjust based on frame rate)
    threshold = 5.0  # pixels per frame

    if avg_change > threshold:
        return "left_to_right"  # Deboarding
    elif avg_change < -threshold:
        return "right_to_left"  # Boarding or ground staff
    else:
        return None  # Stationary or too slow


def update_passenger_flow(
    dets_df: pd.DataFrame,
    roi_passenger_door: Optional[Tuple[int, int, int, int]],
    t_sec: float,
    state: PassengerFlowState,
) -> Dict[str, str]:
    """
    Analyze passenger movement in the passenger_door ROI.

    Returns dict with task statuses:
    {
        "ground_staff_prep": "NOT_STARTED" | "ONGOING" | "DONE",
        "passenger_deboarding": "NOT_STARTED" | "STARTED" | "ONGOING" | "DONE",
        "passenger_boarding": "NOT_STARTED" | "STARTED" | "ONGOING" | "DONE",
    }
    """
    result = {
        "ground_staff_prep": "NOT_STARTED",
        "passenger_deboarding": "NOT_STARTED",
        "passenger_boarding": "NOT_STARTED",
    }

    if roi_passenger_door is None or dets_df is None or dets_df.empty:
        return result

    if "cls_name" not in dets_df.columns or "track_id" not in dets_df.columns:
        return result

    # Filter people in passenger_door ROI
    people = dets_df[dets_df["cls_name"] == "person"]
    people_in_roi = []

    for _, row in people.iterrows():
        bbox = tuple(row["bbox_xyxy"])
        if _in_roi(bbox, roi_passenger_door):
            track_id = int(row["track_id"])
            cx, cy = _get_centroid(bbox)
            people_in_roi.append((track_id, cx, cy))

    # Update position history
    current_tracks = set()
    for track_id, cx, cy in people_in_roi:
        current_tracks.add(track_id)

        if track_id not in state.person_positions:
            state.person_positions[track_id] = []

        # Add current position
        state.person_positions[track_id].append((cx, cy, t_sec))

        # Keep only last 10 positions per person
        if len(state.person_positions[track_id]) > 10:
            state.person_positions[track_id] = state.person_positions[track_id][-10:]

    # Clean up old tracks (not seen for 5 seconds)
    to_remove = []
    for track_id, positions in state.person_positions.items():
        if track_id not in current_tracks:
            if positions:
                _, _, last_t = positions[-1]
                if t_sec - last_t > 5.0:
                    to_remove.append(track_id)

    for track_id in to_remove:
        del state.person_positions[track_id]

    # Analyze movement directions
    left_to_right = 0  # Deboarding
    right_to_left = 0  # Boarding or ground staff

    for track_id in current_tracks:
        positions = state.person_positions.get(track_id, [])
        direction = _detect_movement_direction(positions)

        if direction == "left_to_right":
            left_to_right += 1
        elif direction == "right_to_left":
            right_to_left += 1

    # Update flow counters
    if left_to_right > 0:
        state.last_deboarding_detection = t_sec

    if right_to_left > 0:
        if state.deboarding_started:
            state.last_boarding_detection = t_sec
        else:
            state.last_ground_staff_detection = t_sec

    # ===== GROUND STAFF PREPARATION =====
    # Right-to-left movement BEFORE deboarding starts = door opening prep
    if not state.deboarding_started and not state.ground_staff_prep_done:
        if state.last_ground_staff_detection is not None:
            result["ground_staff_prep"] = "ONGOING"

            # Mark done if no right-to-left movement for 10 seconds
            if t_sec - state.last_ground_staff_detection > 10.0:
                state.ground_staff_prep_done = True
                result["ground_staff_prep"] = "DONE"
        else:
            result["ground_staff_prep"] = "NOT_STARTED"
    else:
        if state.ground_staff_prep_done:
            result["ground_staff_prep"] = "DONE"

    # ===== PASSENGER DEBOARDING =====
    if state.last_deboarding_detection is not None:
        if not state.deboarding_started:
            state.deboarding_started = True
            result["passenger_deboarding"] = "STARTED"
        else:
            # Check if ongoing or done
            time_since_last = t_sec - state.last_deboarding_detection

            if time_since_last < state.deboarding_timeout:
                result["passenger_deboarding"] = "ONGOING"
            else:
                # No deboarding activity for 50+ seconds -> DONE
                result["passenger_deboarding"] = "DONE"
    else:
        result["passenger_deboarding"] = "NOT_STARTED"

    # ===== PASSENGER BOARDING =====
    # Can only start AFTER deboarding is done
    deboarding_done = (
        state.last_deboarding_detection is not None and
        t_sec - state.last_deboarding_detection >= state.deboarding_timeout
    )

    if deboarding_done and state.last_boarding_detection is not None:
        if not state.boarding_started:
            # Need sustained movement for 10 seconds to confirm boarding
            if t_sec - state.last_boarding_detection >= state.boarding_timeout:
                state.boarding_started = True
                result["passenger_boarding"] = "STARTED"
            else:
                result["passenger_boarding"] = "NOT_STARTED"
        else:
            result["passenger_boarding"] = "ONGOING"
    else:
        result["passenger_boarding"] = "NOT_STARTED"

    return result
