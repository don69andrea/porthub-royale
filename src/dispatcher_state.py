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
class DispatcherState:
    run_id: str = "run-0001"

    # tagging
    asset_roles: Dict[int, str] = field(default_factory=dict)

    # per-task state/history
    task_hist: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    task_counters: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # alerts store (dedupe)
    alerts: Dict[str, Any] = field(default_factory=dict)

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
