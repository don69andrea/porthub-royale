# src/infer.py
from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# Frame listing + ROI parsing
# ============================================================
def list_frames(folder: str) -> List[Path]:
    p = Path(folder)
    if not p.exists():
        return []

    exts = ["*.jpg", "*.jpeg", "*.JPG", "*.JPEG", "*.png", "*.PNG"]
    out: List[Path] = []
    for e in exts:
        out.extend(p.glob(e))
    return sorted(out)


def parse_roi(s: str) -> Optional[Tuple[int, int, int, int]]:
    try:
        x1, y1, x2, y2 = [int(x.strip()) for x in s.split(",")]
        return (x1, y1, x2, y2)
    except Exception:
        return None


def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0

    inter = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


class SimpleIoUTracker:
    def __init__(self, iou_match: float = 0.35, max_missed: int = 10):
        self.iou_match = iou_match
        self.max_missed = max_missed
        self.next_id = 1
        self.tracks: Dict[int, Dict] = {}

    def update(self, detections: List[Dict]) -> List[Dict]:
        """
        Assign track_ids using IoU matching.
        IMPORTANT: only match detections to tracks of the SAME cls_name to reduce ID swaps.
        """
        updated = {}
        used = set()

        for det in detections:
            bbox = det["bbox"]
            cls_name = det.get("cls_name")

            best_id = None
            best_iou = 0.0

            for tid, tr in self.tracks.items():
                if tid in used:
                    continue
                # only match same class
                if tr.get("cls_name") != cls_name:
                    continue

                i = _iou(bbox, tr["bbox"])
                if i > best_iou and i >= self.iou_match:
                    best_iou = i
                    best_id = tid

            if best_id is None:
                tid = self.next_id
                self.next_id += 1
                updated[tid] = {"bbox": bbox, "missed": 0, "cls_name": cls_name}
                det["track_id"] = tid
            else:
                updated[best_id] = {"bbox": bbox, "missed": 0, "cls_name": cls_name}
                used.add(best_id)
                det["track_id"] = best_id

        # carry over unmatched tracks for a few frames
        for tid, tr in self.tracks.items():
            if tid not in updated:
                tr["missed"] += 1
                if tr["missed"] <= self.max_missed:
                    updated[tid] = tr

        self.tracks = updated
        return detections


# ============================================================
# Detection stubs / YOLO wrapper
# ============================================================
def load_model(weights: str):
    from ultralytics import YOLO
    return YOLO(weights)


def yolo_detect(model, img: Image.Image, conf: float, iou: float) -> List[Dict]:
    res = model.predict(img, conf=conf, iou=iou, verbose=False)[0]
    out = []
    for b in res.boxes:
        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
        out.append(
            {
                "bbox": (x1, y1, x2, y2),
                "conf": float(b.conf[0]),
                "cls": int(b.cls[0]),
                "cls_name": model.names[int(b.cls[0])],
            }
        )
    return out


def demo_detections(t_sec: float) -> List[Dict]:
    """
    Realistic turnaround scenario for demo mode (no YOLO required).
    Simulates: GPU truck arrives → Fuel truck → Baggage loader → Pushback tug
    Plus: Aircraft, people, and safety scenarios
    """
    t = int(t_sec)
    dets = []

    # Aircraft is always present (parked at gate)
    dets.append({
        "bbox": (400, 200, 850, 600),
        "conf": 0.95,
        "cls": 4,  # airplane
        "cls_name": "airplane"
    })

    # Phase 1: GPU arrives (t=10-120s)
    if 10 <= t <= 120:
        dets.append({
            "bbox": (280, 280, 480, 480),
            "conf": 0.88,
            "cls": 7,  # truck
            "cls_name": "truck"
        })

    # Phase 2: Fuel truck (t=30-180s)
    if 30 <= t <= 180:
        dets.append({
            "bbox": (680, 220, 920, 440),
            "conf": 0.91,
            "cls": 7,  # truck
            "cls_name": "truck"
        })

    # Phase 3: Baggage loader (t=40-200s)
    if 40 <= t <= 200:
        dets.append({
            "bbox": (320, 380, 480, 580),
            "conf": 0.86,
            "cls": 7,  # truck
            "cls_name": "truck"
        })

    # Phase 4: Pushback tug (t=190-250s)
    if 190 <= t <= 250:
        dets.append({
            "bbox": (180, 480, 300, 620),
            "conf": 0.89,
            "cls": 2,  # car
            "cls_name": "car"
        })

    # Ground crew members (moving around)
    if 20 <= t <= 240:
        # Worker near GPU
        if 20 <= t <= 120:
            dets.append({
                "bbox": (350 + (t-20)//5, 450, 390 + (t-20)//5, 550),
                "conf": 0.82,
                "cls": 0,  # person
                "cls_name": "person"
            })

        # Worker near fuel (appears at t=50)
        if 50 <= t <= 180:
            dets.append({
                "bbox": (750, 380, 790, 500),
                "conf": 0.79,
                "cls": 0,
                "cls_name": "person"
            })

        # Worker near baggage (appears at t=60)
        if 60 <= t <= 200:
            dets.append({
                "bbox": (380, 520, 420, 640),
                "conf": 0.84,
                "cls": 0,
                "cls_name": "person"
            })

    # SAFETY SCENARIO: Person enters engine zone (t=100-110) - DANGER!
    if 100 <= t <= 110:
        dets.append({
            "bbox": (450, 380, 490, 500),
            "conf": 0.81,
            "cls": 0,
            "cls_name": "person"
        })

    # SAFETY SCENARIO: Person in pushback area (t=195-205) - WARNING!
    if 195 <= t <= 205:
        dets.append({
            "bbox": (220, 520, 260, 640),
            "conf": 0.77,
            "cls": 0,
            "cls_name": "person"
        })

    return dets


def detections_df_from_tracked(tracked: List[Dict]) -> pd.DataFrame:
    if not tracked:
        return pd.DataFrame()

    rows = []
    for d in tracked:
        rows.append(
            {
                "bbox_xyxy": d["bbox"],
                "conf": d.get("conf", 0.0),
                "cls": d.get("cls"),
                "cls_name": d.get("cls_name"),
                "track_id": d.get("track_id"),
            }
        )
    return pd.DataFrame(rows)


# ============================================================
# Overlay drawing (ROBUST VERSION)
# ============================================================
def _load_font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        try:
            # macOS fallback
            return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
        except Exception:
            return ImageFont.load_default()


def draw_overlay(img: Image.Image, dets_df: pd.DataFrame, title: str = "", rois=None, asset_roles=None) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    font = _load_font(18)  # Increased from 14 to 18 for better visibility
    font_roi = _load_font(16)  # For ROI labels

    w, h = out.size
    if title:
        d.rectangle((0, 0, w, 38), fill=(20, 20, 20))
        d.text((10, 10), title, fill=(240, 240, 240), font=font)

    if rois:
        for k, roi in rois.items():
            if roi is None:
                continue
            x1, y1, x2, y2 = roi
            d.rectangle((x1, y1, x2, y2), outline=(0, 255, 0), width=3)  # Thicker line
            # ROI label with background
            label_y = max(2, y1 - 22)
            d.rectangle((x1, label_y, x1 + len(k)*10 + 8, label_y + 20), fill=(0, 180, 0))
            d.text((x1 + 4, label_y + 2), k, fill=(255, 255, 255), font=font_roi)

    if dets_df is not None and not dets_df.empty:
        for _, r in dets_df.iterrows():
            x1, y1, x2, y2 = [int(v) for v in r["bbox_xyxy"]]
            tid = int(r.get("track_id", -1))
            label = str(r.get("cls_name", "obj"))
            conf = float(r.get("conf", 0.0))

            role = ""
            if asset_roles is not None:
                role = str(asset_roles.get(tid, ""))

            color = (255, 200, 0)
            line_width = 3
            if role and role != "UNASSIGNED":
                color = (0, 220, 255)  # cyan if assigned
                line_width = 4  # Thicker for assigned vehicles

            # Draw bounding box
            d.rectangle((x1, y1, x2, y2), outline=color, width=line_width)

            # Prepare label text
            txt = f"{label} #{tid} {conf:.2f}"
            if role and role != "UNASSIGNED":
                txt += f" [{role}]"

            # Label background (bigger, more visible)
            label_height = 28
            label_width = len(txt) * 10 + 10
            label_y = max(0, y1 - label_height - 2)  # Above box, or at top if no space

            # Draw semi-transparent background for label
            d.rectangle((x1, label_y, x1 + label_width, label_y + label_height), fill=(0, 0, 0, 200))
            # Draw text
            d.text((x1 + 5, label_y + 5), txt, fill=color, font=font)

    return out
