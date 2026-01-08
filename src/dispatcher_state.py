# src/dispatcher_state.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

@dataclass
class EventLogItem:
    t_sec: float
    level: str   # "info" | "task" | "alert" | ...
    msg: str

@dataclass
class TaskHist:
    status: str = "NOT_STARTED"   # NOT_STARTED | ACTIVE | INACTIVE | DONE
    since: Optional[float] = None
    last_seen: Optional[float] = None

@dataclass
class AlertItem:
    id: str
    t_sec: float
    severity: str   # info|warning|critical
    rule_id: str
    message: str
    status: str = "OPEN"          # OPEN | ACK | CLOSED
    last_seen_sec: float = 0.0

@dataclass
class DispatcherState:
    # human-in-the-loop tagging: track_id -> role key
    asset_roles: Dict[int, str] = field(default_factory=dict)

    # task state: task_key -> TaskHist
    task_hist: Dict[str, TaskHist] = field(default_factory=dict)

    # stability counters: key -> {"on":int,"off":int,"last":bool}
    task_counters: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # alerts: alert_id -> AlertItem
    alerts: Dict[str, AlertItem] = field(default_factory=dict)

    # event log
    event_log: List[EventLogItem] = field(default_factory=list)

    # playback
    playback_running: bool = False
    playback_idx: int = 0
    t_sec: float = 0.0

def get_state() -> DispatcherState:
    import streamlit as st
    if "ph_state" not in st.session_state:
        st.session_state.ph_state = DispatcherState()
    return st.session_state.ph_state
