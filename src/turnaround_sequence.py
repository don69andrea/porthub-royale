# src/turnaround_sequence.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ----------------------------
# Sequence specification
# ----------------------------
@dataclass
class StepSpec:
    key: str
    title: str
    deadline_sec: Optional[float] = None          # must be started by this time
    requires_done: List[str] = field(default_factory=list)


@dataclass
class SequenceState:
    current_idx: int = 0
    started_at: Dict[str, float] = field(default_factory=dict)
    done_at: Dict[str, float] = field(default_factory=dict)
    steps: List[Dict[str, Any]] = field(default_factory=list)


def default_sequence() -> List[StepSpec]:
    # keys must match TASKS keys from rules_engine.py:
    # gpu, fueling, baggage, passenger_*, pushback
    # safety_* are handled via alerts, not sequence order
    return [
        StepSpec(key="passenger_deboarding", title="Passenger Deboarding", deadline_sec=180),
        StepSpec(key="gpu", title="GPU connected", deadline_sec=120),
        StepSpec(key="fueling", title="Fueling", requires_done=["gpu"]),
        StepSpec(key="baggage", title="Baggage unloading/loading", requires_done=["gpu"]),
        StepSpec(key="passenger_boarding", title="Passenger Boarding", requires_done=["baggage"]),
        StepSpec(key="pushback", title="Pushback", requires_done=["fueling", "baggage", "passenger_boarding"]),
    ]


# ----------------------------
# Helpers
# ----------------------------
def _status(task_hist: Dict[str, Dict[str, Any]], key: str) -> str:
    return str(task_hist.get(key, {}).get("status", "NOT_STARTED"))


def _is_active(task_hist: Dict[str, Dict[str, Any]], key: str) -> bool:
    status = _status(task_hist, key)
    return status in ["ACTIVE", "STARTED", "ONGOING", "OPERATING", "CONNECTED"]


def _is_inactive(task_hist: Dict[str, Dict[str, Any]], key: str) -> bool:
    return _status(task_hist, key) == "INACTIVE"


def _is_done(task_hist: Dict[str, Dict[str, Any]], key: str) -> bool:
    return _status(task_hist, key) == "DONE"


def _mark_done(task_hist: Dict[str, Dict[str, Any]], key: str, now_t: float, seq_state: SequenceState):
    # set task status DONE once
    th = task_hist.setdefault(key, {"status": "NOT_STARTED", "since": None, "last_seen": None})
    th["status"] = "DONE"
    th["since"] = float(now_t)
    seq_state.done_at[key] = float(now_t)


# ----------------------------
# Sequence update
# ----------------------------
def update_sequence(
    now_t: float,
    task_hist: Dict[str, Dict[str, Any]],
    seq_state: SequenceState,
    alert: Callable[[str, str, str, str, float], None],
    min_active_sec_for_done: float = 5.0,
) -> List[StepSpec]:
    """
    Sequence logic:
    - starts tracked when task becomes ACTIVE first time
    - marks DONE with a simple heuristic: ACTIVE -> INACTIVE after >= min_active_sec_for_done
    - alerts if a step starts before prerequisites are DONE
    - deadline alerts if not started by deadline
    - advances current_idx for UI
    """
    seq = default_sequence()

    # 1) Track starts + out-of-order checks
    for step in seq:
        if _is_active(task_hist, step.key) and step.key not in seq_state.started_at:
            seq_state.started_at[step.key] = now_t

            missing = [k for k in step.requires_done if k not in seq_state.done_at and not _is_done(task_hist, k)]
            if missing:
                alert(
                    alert_id=f"seq_out_of_order_{step.key}",
                    severity="WARNING",
                    rule_id="sequence_order",
                    message=f"{step.title} started before prerequisites DONE: {', '.join(missing)}",
                    now_t=now_t,
                )

    # 2) Mark DONE (heuristic)
    for step in seq:
        if step.key in seq_state.started_at and step.key not in seq_state.done_at and not _is_done(task_hist, step.key):
            started = seq_state.started_at[step.key]
            if _is_inactive(task_hist, step.key) and (now_t - started) >= float(min_active_sec_for_done):
                _mark_done(task_hist, step.key, now_t, seq_state)

    # 3) Deadline checks
    for step in seq:
        if step.deadline_sec is None:
            continue
        if step.key in seq_state.started_at:
            continue
        if now_t >= float(step.deadline_sec):
            alert(
                alert_id=f"seq_deadline_{step.key}",
                severity="WARNING",
                rule_id="sequence_deadline",
                message=f"{step.title} not started by t={step.deadline_sec:.0f}s",
                now_t=now_t,
            )

    # 4) Advance pointer
    while seq_state.current_idx < len(seq):
        k = seq[seq_state.current_idx].key
        if k in seq_state.done_at or _is_done(task_hist, k):
            seq_state.current_idx += 1
        else:
            break

    # 5) Update seq_state.steps for dashboard
    seq_state.steps = [{"key": s.key, "title": s.title} for s in seq]

    return seq
# ----------------------------
