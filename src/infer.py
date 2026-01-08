# src/infer.py
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# Frame helpers
# ============================================================
def list_frames(folder: str) -> List:
    import pathlib

    p = pathlib.Path(folder)
    if not p.exists():
        return []

    exts = ["*.jpg", "*.jpeg", "*.JPG", "*.JPEG", "*.png", "*.PNG"]
    frames = []
    for ext in exts:
        frames.extend(p.glob(ext))

    return sorted(frames)



def parse_roi(txt: str) -> Optional[Tuple[int, int, int, int]]:
    try:
        parts = [int(x.strip()) for x in txt.split(",")]
        if len(parts) != 4:
            return None
        return tuple(parts)  # type: ignore
    except Exception:
        return None


# ============================================================
# Simple IoU Tracker
# ============================================================
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
    return inter / float(area_a + area_b - inter + 1e-6)


class SimpleIoUTracker:
    def __init__(self, iou_match: float = 0.35, max_missed: int = 10):
        self.iou_match = iou_match
        self.max_missed = max_missed
        self.next_id = 1
        self.tracks: Dict[int, Dict] = {}

    def update(self, detections: List[Dict]) -> List[Dict]:
        updated = {}
        used = set()

        for det in detections:
            bbox = det["bbox"]
            best_id = None
            best_iou = 0.0

            for tid, tr in self.tracks.items():
                if tid in used:
                    continue
                i = _iou(bbox, tr["bbox"])
                if i > best_iou and i >= self.iou_match:
                    best_iou = i
                    best_id = tid

            if best_id is None:
                tid = self.next_id
                self.next_id += 1
                updated[tid] = {"bbox": bbox, "missed": 0}
                det["track_id"] = tid
            else:
                updated[best_id] = {"bbox": bbox, "missed": 0}
                used.add(best_id)
                det["track_id"] = best_id

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


def demo_detections(img: Image.Image, t_sec: float) -> List[Dict]:
    w, h = img.size
    return [
        {
            "bbox": (int(w * 0.55), int(h * 0.4), int(w * 0.75), int(h * 0.6)),
            "conf": 0.62,
            "cls": 7,
            "cls_name": "truck",
        }
    ]


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
        return ImageFont.load_default()


def draw_overlay(
    img: Image.Image,
    dets_df: pd.DataFrame,
    title: str,
    rois: Optional[Dict[str, Tuple[int, int, int, int]]] = None,
    asset_roles: Optional[Dict[int, str]] = None,
) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    font = _load_font(16)

    # Title
    d.rectangle((0, 0, out.size[0], 34), fill=(0, 0, 0))
    d.text((10, 8), title, fill=(255, 255, 255), font=font)

    # ROIs (safe)
    if rois:
        for key, roi in rois.items():
            color = (0, 180, 255)
            if key == "engine":
                color = (255, 80, 80)
            elif key in ("nose", "fuel", "belly"):
                color = (200, 200, 60)

            d.rectangle(roi, outline=color, width=3)
            d.text((roi[0] + 6, roi[1] + 6), f"{key.upper()} ROI", fill=color, font=font)

    # Detections
    if dets_df is not None and not dets_df.empty:
        for _, r in dets_df.iterrows():
            x1, y1, x2, y2 = r["bbox_xyxy"]
            tid = r.get("track_id")
            label = r.get("cls_name", "obj")
            conf = r.get("conf", 0.0)

            role = asset_roles.get(tid) if asset_roles else None

            # Always visible
            color = (0, 255, 0)  # bright green default
            if role and role != "UNASSIGNED":
                color = (0, 220, 255)  # cyan if assigned

            d.rectangle((x1, y1, x2, y2), outline=color, width=2)
            txt = f"{label} #{tid} {conf:.2f}"
            if role and role != "UNASSIGNED":
                txt += f" [{role}]"

            # label background for readability
            bg = (0, 0, 0)
            d.rectangle((x1, y1, x1 + 220, y1 + 24), fill=bg)
            d.text((x1 + 4, y1 + 4), txt, fill=color, font=font)


    return out
