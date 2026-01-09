# tests/test_tracker.py
"""
Unit tests for SimpleIoUTracker
"""
import pytest
from src.infer import SimpleIoUTracker, _iou


class TestIoU:
    """Test IoU calculation"""

    def test_iou_identical_boxes(self):
        """Identical boxes should have IoU = 1.0"""
        box = (100, 100, 200, 200)
        assert _iou(box, box) == 1.0

    def test_iou_no_overlap(self):
        """Non-overlapping boxes should have IoU = 0.0"""
        box_a = (0, 0, 50, 50)
        box_b = (100, 100, 150, 150)
        assert _iou(box_a, box_b) == 0.0

    def test_iou_partial_overlap(self):
        """Partially overlapping boxes should have 0 < IoU < 1"""
        box_a = (0, 0, 100, 100)
        box_b = (50, 50, 150, 150)
        iou_value = _iou(box_a, box_b)
        assert 0 < iou_value < 1
        # Expected: overlap is 50x50=2500, union is 10000+10000-2500=17500
        # IoU = 2500/17500 ≈ 0.1429
        assert abs(iou_value - 0.1429) < 0.01

    def test_iou_symmetric(self):
        """IoU should be symmetric"""
        box_a = (10, 10, 60, 60)
        box_b = (30, 30, 80, 80)
        assert _iou(box_a, box_b) == _iou(box_b, box_a)


class TestSimpleIoUTracker:
    """Test tracking functionality"""

    def test_tracker_initialization(self):
        """Tracker should initialize correctly"""
        tracker = SimpleIoUTracker(iou_match=0.3, max_missed=5)
        assert tracker.next_id == 1
        assert tracker.iou_match == 0.3
        assert tracker.max_missed == 5
        assert len(tracker.tracks) == 0

    def test_tracker_assigns_new_ids(self):
        """First detections should get new track IDs"""
        tracker = SimpleIoUTracker()
        detections = [
            {"bbox": (100, 100, 200, 200), "cls_name": "truck", "conf": 0.9},
            {"bbox": (300, 300, 400, 400), "cls_name": "car", "conf": 0.8}
        ]

        tracked = tracker.update(detections)

        assert len(tracked) == 2
        assert tracked[0]["track_id"] == 1
        assert tracked[1]["track_id"] == 2

    def test_tracker_maintains_ids_on_movement(self):
        """Track IDs should persist for moving objects"""
        tracker = SimpleIoUTracker(iou_match=0.3, max_missed=10)

        # Frame 1
        dets_1 = [{"bbox": (100, 100, 200, 200), "cls_name": "truck", "conf": 0.9}]
        tracked_1 = tracker.update(dets_1)
        track_id_1 = tracked_1[0]["track_id"]

        # Frame 2: object moved slightly (high IoU with frame 1)
        dets_2 = [{"bbox": (110, 105, 210, 205), "cls_name": "truck", "conf": 0.88}]
        tracked_2 = tracker.update(dets_2)

        # Should keep same ID
        assert tracked_2[0]["track_id"] == track_id_1

    def test_tracker_respects_class_matching(self):
        """Should only match detections with same class"""
        tracker = SimpleIoUTracker(iou_match=0.3)

        # Frame 1: truck
        dets_1 = [{"bbox": (100, 100, 200, 200), "cls_name": "truck", "conf": 0.9}]
        tracked_1 = tracker.update(dets_1)
        truck_id = tracked_1[0]["track_id"]

        # Frame 2: car in same location (should NOT reuse truck ID)
        dets_2 = [{"bbox": (105, 105, 205, 205), "cls_name": "car", "conf": 0.85}]
        tracked_2 = tracker.update(dets_2)

        assert tracked_2[0]["track_id"] != truck_id

    def test_tracker_handles_disappearance(self):
        """Should handle temporarily missing objects"""
        tracker = SimpleIoUTracker(iou_match=0.3, max_missed=3)

        # Frame 1: object present
        dets_1 = [{"bbox": (100, 100, 200, 200), "cls_name": "truck", "conf": 0.9}]
        tracked_1 = tracker.update(dets_1)
        track_id = tracked_1[0]["track_id"]

        # Frames 2-4: object missing (but within max_missed)
        for _ in range(3):
            tracker.update([])

        # Track should still exist in internal state
        assert track_id in tracker.tracks

        # Frame 5: one more frame missing → track should be dropped
        tracker.update([])
        assert track_id not in tracker.tracks

    def test_tracker_id_reuse_prevention(self):
        """Should not reuse IDs for different objects simultaneously"""
        tracker = SimpleIoUTracker()

        # Two objects in frame 1
        dets = [
            {"bbox": (100, 100, 200, 200), "cls_name": "truck", "conf": 0.9},
            {"bbox": (300, 300, 400, 400), "cls_name": "truck", "conf": 0.85}
        ]
        tracked = tracker.update(dets)

        ids = {d["track_id"] for d in tracked}
        assert len(ids) == 2  # Should have 2 unique IDs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
