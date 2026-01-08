# src/infer.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None


# ----------------------------
# Utils
# ----------------------------
def list_frames(folder: str, suffixes: Tuple[str, ...] = (".jpg", ".jpeg", ".png")) -> List[Path]:
    p = Path(folder)
    if not p.exists():
        return []
    files = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in suffixes]
    files.sort(key=lambda x: x.name)
    return files


def iou_xyxy(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = max(1e-6, area_a + area_b - inter)
    return float(inter / union)


def parse_roi(text: str) -> Optional[Tuple[int, int, int, int]]:
    # erwartet "x1,y1,x2,y2"
    if not text:
        return None
    try:
        parts = [int(p.strip()) for p in text.split(",")]
        if len(parts) != 4:
            return None
        x1, y1, x2, y2 = parts
        x1, x2 = sorted([x1, x2])
        y1, y2 = sorted([y1, y2])
        if x2 <= x1 or y2 <= y1:
            return None
        return (x1, y1, x2, y2)
    except Exception:
        return None


def _load_font(size: int = 16) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("Arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


# ----------------------------
# Simple Tracker (IoU association)
# ----------------------------
@dataclass
class TrackedDet:
    track_id: int
    cls_name: str
    conf: float
    bbox_xyxy: Tuple[int, int, int, int]


class SimpleIoUTracker:
    """
    Minimal tracker: assigns track IDs based on IoU matching to previous frame.
    Good enough for demo / jury prototype.
    """
    def __init__(self, iou_match: float = 0.35, max_missed: int = 10):
        self.iou_match = iou_match
        self.max_missed = max_missed
        self.next_id = 1
        self.tracks: Dict[int, Dict] = {}  # id -> {"bbox": xyxy, "cls": str, "missed": int}

    def update(self, dets: List[Tuple[str, float, Tuple[int, int, int, int]]]) -> List[TrackedDet]:
        # increment missed
        for tid in list(self.tracks.keys()):
            self.tracks[tid]["missed"] += 1
            if self.tracks[tid]["missed"] > self.max_missed:
                del self.tracks[tid]

        assigned: List[TrackedDet] = []
        used_track_ids = set()

        for cls_name, conf, bbox in dets:
            best_tid = None
            best_iou = 0.0
            for tid, tr in self.tracks.items():
                if tid in used_track_ids:
                    continue
                # prefer same class a bit by only matching same class (more stable)
                if tr["cls"] != cls_name:
                    continue
                val = iou_xyxy(bbox, tr["bbox"])
                if val > best_iou:
                    best_iou = val
                    best_tid = tid

            if best_tid is not None and best_iou >= self.iou_match:
                # update track
                self.tracks[best_tid]["bbox"] = bbox
                self.tracks[best_tid]["missed"] = 0
                used_track_ids.add(best_tid)
                assigned.append(TrackedDet(best_tid, cls_name, conf, bbox))
            else:
                # create new track
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = {"bbox": bbox, "cls": cls_name, "missed": 0}
                used_track_ids.add(tid)
                assigned.append(TrackedDet(tid, cls_name, conf, bbox))

        return assigned


# ----------------------------
# YOLO wrappers
# ----------------------------
def load_model(weights_path: str):
    if YOLO is None:
        raise RuntimeError("Ultralytics not installed. Use Demo Mode or `pip install ultralytics`.")
    return YOLO(weights_path)


def yolo_detect(model, img: Image.Image, conf: float, iou: float) -> List[Tuple[str, float, Tuple[int, int, int, int]]]:
    arr = np.array(img)
    res = model.predict(arr, conf=conf, iou=iou, verbose=False)[0]
    if res.boxes is None:
        return []
    names = res.names

    boxes = res.boxes.xyxy.cpu().numpy()
    cls_ids = res.boxes.cls.cpu().numpy().astype(int)
    confs = res.boxes.conf.cpu().numpy()

    out: List[Tuple[str, float, Tuple[int, int, int, int]]] = []
    for (x1, y1, x2, y2), cid, c in zip(boxes, cls_ids, confs):
        out.append((str(names.get(int(cid), str(cid))), float(c), (int(x1), int(y1), int(x2), int(y2))))
    return out


# ----------------------------
# Demo mode detections (stable-ish)
# ----------------------------
def demo_detections(img: Image.Image, t: float) -> List[Tuple[str, float, Tuple[int, int, int, int]]]:
    w, h = img.size
    out = []

    # person(s)
    px = int(w * 0.55 + 25 * np.sin(t / 3))
    py = int(h * 0.60 + 15 * np.cos(t / 2))
    out.append(("person", 0.78, (px, py, px + 55, py + 130)))
    out.append(("person", 0.70, (px + 65, py + 10, px + 110, py + 135)))

    # truck (proxy for GPU/Fuel/etc.)
    vx = int(w * 0.72 + 35 * np.sin(t / 4))
    vy = int(h * 0.22)
    out.append(("truck", 0.62, (vx, vy, vx + 110, vy + 65)))

    # car (proxy)
    cx = int(w * 0.62 + 20 * np.cos(t / 5))
    cy = int(h * 0.72)
    out.append(("car", 0.58, (cx, cy, cx + 90, cy + 50)))

    return out


# ----------------------------
# Convert to dets_df used by app.py
# ----------------------------
def detections_df_from_tracked(tracked: List[TrackedDet]) -> pd.DataFrame:
    rows = []
    for d in tracked:
        rows.append(
            {
                "track_id": int(d.track_id),
                "cls_name": str(d.cls_name),
                "conf": float(d.conf),
                "bbox_xyxy": tuple(map(float, d.bbox_xyxy)),  # keep float for center calculations
            }
        )
    return pd.DataFrame(rows)


def draw_overlay(
    img: Image.Image,
    dets_df: pd.DataFrame,
    title: str,
    rois: Dict[str, Optional[Tuple[int, int, int, int]]],
    asset_roles: Optional[Dict[int, str]] = None,
) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    font = _load_font(16)

    # ROIs
    for key, roi in rois.items():
        if roi is None:
            continue
        # different colors per ROI
        color = (0, 180, 255)
        if key == "engine":
            color = (255, 80, 80)
        elif key in ("nose", "fuel", "belly"):
            color = (200, 200, 60)
        d.rectangle(roi, outline=color, width=3)
        d.text((roi[0] + 6, roi[1] + 6), f"{key.upper()} ROI", fill=color, font=font)

    # detections
    if dets_df is not None and not dets_df.empty:
        for _, r in dets_df.iterrows():
            x1, y1, x2, y2 = map(int, r["bbox_xyxy"])
            cls_name = str(r["cls_name"])
            conf = float(r["conf"])
            tid = int(r["track_id"])

            role = ""
            if asset_roles is not None and tid in asset_roles and asset_roles[tid] != "UNASSIGNED":
                role = f" · {asset_roles[tid]}"

            d.rectangle((x1, y1, x2, y2), outline=(0, 255, 0), width=3)
            label = f"{cls_name} #{tid} {conf:.2f}{role}"
            tw = d.textbbox((0, 0), label, font=font)[2]
            th = d.textbbox((0, 0), label, font=font)[3]
            d.rectangle((x1, y1 - th - 6, x1 + tw + 10, y1), fill=(0, 0, 0))
            d.text((x1 + 5, y1 - th - 4), label, fill=(0, 255, 0), font=font)

    # header
    d.rectangle((0, 0, out.size[0], 34), fill=(0, 0, 0))
    d.text((10, 7), title, fill=(255, 255, 255), font=font)

    return out
