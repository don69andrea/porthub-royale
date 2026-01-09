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
    return []


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


def draw_overlay(img: Image.Image, dets_df: pd.DataFrame, title: str = "", rois=None, asset_roles=None) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    font = _load_font(14)

    w, h = out.size
    if title:
        d.rectangle((0, 0, w, 38), fill=(20, 20, 20))
        d.text((10, 10), title, fill=(240, 240, 240), font=font)

    if rois:
        for k, roi in rois.items():
            if roi is None:
                continue
            x1, y1, x2, y2 = roi
            d.rectangle((x1, y1, x2, y2), outline=(0, 255, 0), width=2)
            d.text((x1 + 4, max(2, y1 - 18)), k, fill=(0, 255, 0), font=font)

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
            if role and role != "UNASSIGNED":
                color = (0, 220, 255)  # cyan if assigned

            d.rectangle((x1, y1, x2, y2), outline=color, width=2)
            txt = f"{label} #{tid} {conf:.2f}"
            if role and role != "UNASSIGNED":
                txt += f" [{role}]"

            d.rectangle((x1, y1, x1 + 260, y1 + 24), fill=(0, 0, 0))
            d.text((x1 + 4, y1 + 4), txt, fill=color, font=font)

    return out
