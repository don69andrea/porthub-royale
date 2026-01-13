# src/passenger_flow.py
"""
Passenger flow detection with movement direction analysis.
Distinguishes between:
- Unboarding (left-to-right movement)
- Boarding (right-to-left movement)
- Ground staff preparation (right-to-left before unboarding)
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
    left_to_right_count: int = 0  # Unboarding
    right_to_left_count: int = 0  # Boarding / Ground staff

    # Last detection times
    last_unboarding_detection: Optional[float] = None
    last_boarding_detection: Optional[float] = None
    last_ground_staff_detection: Optional[float] = None

    # Task states
    unboarding_started: bool = False
    boarding_started: bool = False
    ground_staff_prep_done: bool = False

    # First detection timestamps for confirmation delay
    first_unboarding_seen: Optional[float] = None
    first_boarding_seen: Optional[float] = None

    # Timing thresholds
    unboarding_min_duration: float = 20.0  # Must stay ACTIVE for min 20s
    unboarding_timeout: float = 60.0  # No movement for 60s -> DONE (after min duration)
    boarding_timeout: float = 10.0    # Movement for 10s -> START
    boarding_min_duration: float = 5.0  # Must stay ACTIVE for min 5s


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
        "left_to_right" - Person moving right (unboarding)
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
        return "left_to_right"  # Unboarding
    elif avg_change < -threshold:
        return "right_to_left"  # Boarding or ground staff
    else:
        return None  # Stationary or too slow


def update_passenger_flow(
    dets_df: pd.DataFrame,
    roi_passenger_door: Optional[Tuple[int, int, int, int]],
    t_sec: float,
    state: PassengerFlowState,
    fingerdock_status: str = "UNDOCKED",
) -> Dict[str, str]:
    """
    Analyze passenger movement in the passenger_door ROI.

    Returns dict with task statuses:
    {
        "ground_staff_prep": "NOT_STARTED" | "ONGOING" | "DONE",
        "passenger_unboarding": "NOT_STARTED" | "STARTED" | "ONGOING" | "DONE",
        "passenger_boarding": "NOT_STARTED" | "STARTED" | "ONGOING" | "DONE",
    }
    """
    result = {
        "ground_staff_prep": "NOT_STARTED",
        "passenger_unboarding": "NOT_STARTED",
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
    left_to_right = 0  # Unboarding
    right_to_left = 0  # Boarding or ground staff

    for track_id in current_tracks:
        positions = state.person_positions.get(track_id, [])
        direction = _detect_movement_direction(positions)

        if direction == "left_to_right":
            left_to_right += 1
        elif direction == "right_to_left":
            right_to_left += 1

    # Count people currently in ROI
    people_in_roi_count = len(people_in_roi)

    # ===== GROUND STAFF PREPARATION =====
    # Right-to-left movement BEFORE unboarding starts = door opening prep
    if not state.unboarding_started and not state.ground_staff_prep_done:
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

    # ===== PASSENGER UNBOARDING =====
    # IGNORE MOVEMENT DIRECTION - use simple person presence detection
    # CRITICAL: Task can ONLY be active when Fingerdock is DOCKED
    # CRITICAL: Each person detection adds 20 seconds to activity time
    # NOTE: For future detection systems with better direction tracking:
    #       - Left-to-right (L→R) movement = Unboarding (passengers exiting aircraft)
    #       - Right-to-left (R←L) movement = Boarding (passengers entering aircraft)
    # TODO: Future enhancement - get passenger manifest count and auto-mark DONE when count matches

    # Check for ANY person presence AND fingerdock docked
    # ONLY update detection time if unboarding is NOT done yet
    if people_in_roi_count > 0 and fingerdock_status == "DOCKED" and not state.boarding_started:
        if state.first_unboarding_seen is None:
            # First time seeing a person - initialize timestamp
            state.first_unboarding_seen = t_sec
        # Update last detection time (extends activity window by 20 seconds)
        state.last_unboarding_detection = t_sec

    # Logic: Task is ONLY active when fingerdock is DOCKED
    # Each person detection adds 20 seconds of activity
    # Task must stay ACTIVE for minimum 20 seconds before it can end
    # Task is DONE after 60 seconds of no detection (or when passenger count matches manifest)
    if not state.unboarding_started:
        # Task not started yet - check if we should start
        if state.last_unboarding_detection is not None and fingerdock_status == "DOCKED":
            state.unboarding_started = True
            result["passenger_unboarding"] = "STARTED"
        else:
            result["passenger_unboarding"] = "NOT_STARTED"
    elif state.boarding_started:
        # Boarding has started - unboarding is definitely DONE
        result["passenger_unboarding"] = "DONE"
    else:
        # Task is started but not yet boarding - check if still ONGOING or DONE
        if fingerdock_status != "DOCKED":
            # Fingerdock undocked - mark as DONE
            result["passenger_unboarding"] = "DONE"
        elif state.last_unboarding_detection is None:
            # No detection time set - should not happen, mark as DONE
            result["passenger_unboarding"] = "DONE"
        else:
            time_since_last = t_sec - state.last_unboarding_detection
            time_since_start = t_sec - state.first_unboarding_seen if state.first_unboarding_seen else 0

            # Force ONGOING for at least 20 seconds after task starts
            if time_since_start < 20.0:
                result["passenger_unboarding"] = "ONGOING"
            elif time_since_last < 20.0:
                result["passenger_unboarding"] = "ONGOING"
            elif time_since_last < 60.0:
                result["passenger_unboarding"] = "ONGOING"
            else:
                # No person detected for 60+ seconds -> DONE
                result["passenger_unboarding"] = "DONE"

    # ===== PASSENGER BOARDING =====
    # Can ONLY start AFTER unboarding is DONE (not just inactive, but actually completed)
    # CRITICAL: Task can ONLY be active when Fingerdock is DOCKED
    # CRITICAL: Each person detection adds 20 seconds to activity time
    # TODO: Future enhancement - get passenger manifest count and auto-mark DONE when count matches
    unboarding_is_done = (result["passenger_unboarding"] == "DONE")

    if unboarding_is_done:
        # Check for ANY person presence AND fingerdock DOCKED
        if people_in_roi_count > 0 and fingerdock_status == "DOCKED":
            # Person detected AND fingerdock docked!
            if state.first_boarding_seen is None:
                state.first_boarding_seen = t_sec
            # Each detection extends the activity window by 20 seconds
            state.last_boarding_detection = t_sec

        # Logic: Task is ONLY active when fingerdock is DOCKED
        # Each person detection adds 20 seconds of activity
        # Task must stay ACTIVE for minimum 20 seconds before it can end
        # Task is DONE after 60 seconds of no detection
        if state.last_boarding_detection is not None and fingerdock_status == "DOCKED":
            time_since_last_boarding = t_sec - state.last_boarding_detection

            if not state.boarding_started:
                # Start immediately on first person detection
                state.boarding_started = True
                result["passenger_boarding"] = "STARTED"
            else:
                # Already started - calculate time since task started
                time_since_start = t_sec - state.first_boarding_seen if state.first_boarding_seen else 0

                # Force ONGOING for at least 20 seconds after task starts
                if time_since_start < 20.0:
                    # Must stay ACTIVE for minimum 20 seconds
                    result["passenger_boarding"] = "ONGOING"
                elif time_since_last_boarding < 20.0:
                    # Within 20 second window from last detection (after minimum period)
                    result["passenger_boarding"] = "ONGOING"
                elif time_since_last_boarding < 60.0:
                    # Between 20-60 seconds - still ONGOING, waiting for next passenger or timeout
                    result["passenger_boarding"] = "ONGOING"
                else:
                    # No person detected for 60+ seconds -> DONE
                    # TODO: Also mark DONE if passenger count == manifest count
                    result["passenger_boarding"] = "DONE"
        elif state.boarding_started and fingerdock_status != "DOCKED":
            # Fingerdock undocked while boarding was active - mark as DONE
            result["passenger_boarding"] = "DONE"
        elif state.last_boarding_detection is not None and fingerdock_status != "DOCKED":
            # Was active before, but fingerdock no longer docked
            result["passenger_boarding"] = "NOT_STARTED"
        else:
            result["passenger_boarding"] = "NOT_STARTED"
    else:
        # Unboarding not done yet - boarding cannot start
        result["passenger_boarding"] = "NOT_STARTED"

    return result
