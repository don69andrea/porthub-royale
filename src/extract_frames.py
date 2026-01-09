import cv2
from pathlib import Path

import signal
import sys
import time

def _sig_handler(signum, frame):
    print(f"\n[Signal] Received: {signum} at {time.strftime('%H:%M:%S')}")
    sys.stdout.flush()
    raise KeyboardInterrupt

signal.signal(signal.SIGINT, _sig_handler)
signal.signal(signal.SIGTERM, _sig_handler)
signal.signal(signal.SIGHUP, _sig_handler)


def extract_frames(
    video_path: Path,
    output_dir: Path,
    target_fps: float = 1.0,
) -> None:
    """
    Extrahiert Frames aus einem Video und speichert sie als Bilder.

    video_path: Pfad zur Video Datei
    output_dir: Ordner, in dem die Bilder gespeichert werden
    target_fps: Anzahl Bilder pro Sekunde, die extrahiert werden sollen
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found at {video_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video {video_path}")

    original_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = total_frames / original_fps if original_fps > 0 else 0

    print(f"Video: {video_path.name}")
    print(f"Original FPS: {original_fps:.2f}")
    print(f"Total frames: {total_frames}")
    print(f"Duration: {duration_sec:.1f} seconds")

    if original_fps <= 0:
        raise RuntimeError("Could not read FPS from video")

    # alle n-te Frame speichern, so dass ungefähr target_fps erreicht wird
    frame_interval = max(int(round(original_fps / target_fps)), 1)
    print(f"Saving every {frame_interval} frame(s) to get ~{target_fps} FPS")

    frame_idx = 0
    saved_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break  # Ende des Videos

        if frame_idx % frame_interval == 0:
            frame_filename = output_dir / f"frame_{saved_count:05d}.jpg"
            cv2.imwrite(str(frame_filename), frame)
            saved_count += 1

        frame_idx += 1

    cap.release()
    print(f"Saved {saved_count} frames to {output_dir}")


if __name__ == "__main__":
    # Projektwurzel = zwei Ebenen über dieser Datei
    project_root = Path(__file__).resolve().parents[1]

    video_path = project_root / "data" / "raw_video" / "10Minuten_Turnaround.mp4"
    output_dir = project_root / "data" / "frames"

    extract_frames(video_path, output_dir, target_fps=1.0)
