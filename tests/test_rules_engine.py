# tests/test_rules_engine.py
"""
Unit tests for rules engine
"""
import pytest
import pandas as pd
from src.rules_engine import (
    _in_roi,
    _ensure_task,
    _set_status,
    compute_alerts_df,
    AlertItem
)


class TestROI:
    """Test ROI detection"""

    def test_in_roi_center(self):
        """Object centered in ROI should be detected"""
        bbox = (100, 100, 200, 200)  # center at (150, 150)
        roi = (50, 50, 250, 250)
        assert _in_roi(bbox, roi) is True

    def test_in_roi_edge(self):
        """Object at ROI edge should be detected"""
        bbox = (100, 100, 200, 200)  # center at (150, 150)
        roi = (150, 150, 300, 300)  # ROI starts at center point
        assert _in_roi(bbox, roi) is True

    def test_outside_roi(self):
        """Object outside ROI should not be detected"""
        bbox = (100, 100, 200, 200)  # center at (150, 150)
        roi = (300, 300, 400, 400)
        assert _in_roi(bbox, roi) is False

    def test_in_roi_x_only(self):
        """Object with center inside X but outside Y should fail"""
        bbox = (100, 100, 200, 200)  # center at (150, 150)
        roi = (50, 200, 250, 300)  # x matches, y doesn't
        assert _in_roi(bbox, roi) is False


class TestTaskManagement:
    """Test task state management"""

    def test_ensure_task_creates_new(self):
        """Should create task if not exists"""
        task_hist = {}
        result = _ensure_task(task_hist, "fueling")

        assert "fueling" in task_hist
        assert task_hist["fueling"]["status"] == "NOT_STARTED"
        assert task_hist["fueling"]["since"] is None
        assert task_hist["fueling"]["last_seen"] is None

    def test_ensure_task_returns_existing(self):
        """Should return existing task without modification"""
        task_hist = {
            "fueling": {"status": "ACTIVE", "since": 10.0, "last_seen": 15.0}
        }
        result = _ensure_task(task_hist, "fueling")

        assert result["status"] == "ACTIVE"
        assert result["since"] == 10.0

    def test_set_status_new_status(self):
        """Should update status and log transition"""
        task_hist = {}
        logs = []

        def mock_log(level, t, msg):
            logs.append((level, t, msg))

        _set_status(task_hist, "fueling", "ACTIVE", 20.0, mock_log)

        assert task_hist["fueling"]["status"] == "ACTIVE"
        assert task_hist["fueling"]["since"] == 20.0
        assert task_hist["fueling"]["last_seen"] == 20.0
        assert len(logs) == 1
        assert "fueling => ACTIVE" in logs[0][2]

    def test_set_status_same_status(self):
        """Should update last_seen but not log if status unchanged"""
        task_hist = {
            "fueling": {"status": "ACTIVE", "since": 10.0, "last_seen": 15.0}
        }
        logs = []

        def mock_log(level, t, msg):
            logs.append((level, t, msg))

        _set_status(task_hist, "fueling", "ACTIVE", 20.0, mock_log)

        assert task_hist["fueling"]["status"] == "ACTIVE"
        assert task_hist["fueling"]["since"] == 10.0  # Unchanged
        assert task_hist["fueling"]["last_seen"] == 20.0  # Updated
        assert len(logs) == 0  # No log for unchanged status


class TestAlertsCompilation:
    """Test alert compilation and DataFrame generation"""

    def test_compute_alerts_df_empty(self):
        """Should return empty DataFrame with correct columns"""
        task_hist = {}
        alerts = {}
        logs = []

        def mock_log(level, t, msg):
            logs.append((level, t, msg))

        df = compute_alerts_df(100.0, task_hist, alerts, mock_log)

        assert isinstance(df, pd.DataFrame)
        assert "severity" in df.columns
        assert "message" in df.columns
        assert len(df) == 0

    def test_compute_alerts_df_with_alerts(self):
        """Should include open alerts in DataFrame"""
        task_hist = {}
        alerts = {
            "test_alert": AlertItem(
                alert_id="test_alert",
                severity="WARNING",
                rule_id="test_rule",
                message="Test alert message",
                first_seen=10.0,
                last_seen=20.0,
                status="OPEN"
            )
        }
        logs = []

        def mock_log(level, t, msg):
            logs.append((level, t, msg))

        df = compute_alerts_df(100.0, task_hist, alerts, mock_log)

        assert len(df) == 1
        assert df.iloc[0]["severity"] == "WARNING"
        assert "Test alert message" in df.iloc[0]["message"]

    def test_compute_alerts_df_sorts_by_severity(self):
        """Should sort alerts by severity (CRITICAL first)"""
        task_hist = {}
        alerts = {
            "info_alert": AlertItem(
                alert_id="info_alert",
                severity="INFO",
                rule_id="info_rule",
                message="Info message",
                first_seen=10.0,
                last_seen=20.0,
                status="OPEN"
            ),
            "critical_alert": AlertItem(
                alert_id="critical_alert",
                severity="CRITICAL",
                rule_id="critical_rule",
                message="Critical message",
                first_seen=10.0,
                last_seen=20.0,
                status="OPEN"
            ),
            "warning_alert": AlertItem(
                alert_id="warning_alert",
                severity="WARNING",
                rule_id="warning_rule",
                message="Warning message",
                first_seen=10.0,
                last_seen=20.0,
                status="OPEN"
            )
        }
        logs = []

        def mock_log(level, t, msg):
            logs.append((level, t, msg))

        df = compute_alerts_df(100.0, task_hist, alerts, mock_log)

        assert len(df) == 3
        # First should be CRITICAL
        assert df.iloc[0]["severity"] == "CRITICAL"
        # Second should be WARNING
        assert df.iloc[1]["severity"] == "WARNING"
        # Last should be INFO
        assert df.iloc[2]["severity"] == "INFO"

    def test_safety_engine_zone_alert(self):
        """Should create CRITICAL alert when person in engine zone"""
        task_hist = {
            "safety_engine_clear": {"status": "ACTIVE", "since": 100.0, "last_seen": 105.0}
        }
        alerts = {}
        logs = []

        def mock_log(level, t, msg):
            logs.append((level, t, msg))

        df = compute_alerts_df(110.0, task_hist, alerts, mock_log)

        # Should have created alert
        assert "engine_zone_not_clear" in alerts
        assert alerts["engine_zone_not_clear"].severity == "CRITICAL"
        assert "DANGER" in alerts["engine_zone_not_clear"].message

    def test_safety_pushback_zone_alert(self):
        """Should create WARNING alert when person in pushback zone"""
        task_hist = {
            "safety_pushback_clear": {"status": "ACTIVE", "since": 200.0, "last_seen": 205.0}
        }
        alerts = {}
        logs = []

        def mock_log(level, t, msg):
            logs.append((level, t, msg))

        df = compute_alerts_df(210.0, task_hist, alerts, mock_log)

        # Should have created alert
        assert "pushback_area_not_clear" in alerts
        assert alerts["pushback_area_not_clear"].severity == "WARNING"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
