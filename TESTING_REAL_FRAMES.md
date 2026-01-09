# Testing with Real Frames (data/frames/)

**Updated**: January 9, 2026

---

## ✅ Changes Made

- **Default frames folder** changed from `data/frames_1fps` → `data/frames`
- Config updated in [app.py](app.py:312) and [config/settings.yaml](config/settings.yaml:25)
- Now uses **601 frames** (~10 minutes) instead of 135 frames

---

## 🚀 How to Test

### 1. Start the Application

```bash
streamlit run app.py
```

### 2. Configuration in Sidebar

**Frames folder**: Should now show `data/frames` (default)

**Options for testing**:

#### Option A: Demo Mode (Recommended für erste Tests)
✅ Check **"Demo Mode (no ML deps)"**
- Synthetic detections (kein YOLO benötigt)
- Schnell zum Testen der UI/Logic

#### Option B: Real YOLO Detection
❌ Uncheck "Demo Mode"
- Uses YOLOv8 on real frames
- **Requires**: YOLO model (wird automatisch downloaded beim ersten Mal)
- **Performance**: ~5 FPS on CPU, ~30 FPS on GPU

---

## 📊 Frame Comparison

| Folder | Frames | Duration | Use Case |
|--------|--------|----------|----------|
| `data/frames_1fps` | 135 | ~2.25 min | Quick tests |
| `data/frames` | **601** | **~10 min** | **Full turnaround** ⭐ |

---

## 🎯 What to Watch For (Real Frames)

### With YOLO Detection:

1. **Aircraft Detection**:
   - Should detect airplane in most frames
   - Check confidence (should be >0.9)

2. **Vehicle Tracking**:
   - Watch track IDs in overlay
   - IDs should stay consistent (no wild jumping)
   - If IDs change: Normal! Use **Asset Tagging** to maintain roles

3. **Person Detection**:
   - Ground crew should be detected
   - Watch for safety alerts when people enter ROIs

4. **ROI Calibration**:
   - Check if ROIs match your video perspective
   - **If not aligned**: Adjust in sidebar or [config/settings.yaml](config/settings.yaml)

### ROI Adjustment (if needed):

Current defaults (in sidebar):
```
nose:      260,250,620,520
fuel:      620,170,980,520
belly:     250,320,520,650
aircraft:  250,120,980,690
engine:    330,300,620,560
pushback:  120,420,330,680
```

**Tip**: Enable "Show ROIs overlay" to see zones on video

---

## 🐛 Troubleshooting

### Issue: "No frames found"
**Check**:
```bash
ls data/frames/*.jpg | wc -l
# Should show: 601
```

**Solution**: Make sure frames exist, or change path in sidebar

---

### Issue: YOLO model download slow
**First run only**: YOLOv8n model (~6MB) downloads automatically

**Workaround**: Use Demo Mode while it downloads

---

### Issue: Performance is slow
**CPU mode**:
- Frames: 601 → ~120 seconds to process all (5 FPS)
- **Tip**: Lower playback FPS to 2-3 for smoother UI

**GPU mode**:
- Should process at ~30 FPS
- Check: `nvidia-smi` (if CUDA available)

---

### Issue: Detections look wrong
**Check confidence threshold**:
- Sidebar → "Confidence" slider
- Default: 0.20 (lower = more detections, more false positives)
- Try: 0.30-0.40 for cleaner results

---

## 📝 Asset Tagging Workflow

With 601 frames (10 min video), you'll have **more vehicles** appearing:

### Expected Timeline (approximate):

**Early Phase (0-2min)**:
- Aircraft arrives/is parked
- GPU truck approaches
- → **Tag as "GPU"**

**Mid Phase (2-6min)**:
- Fuel truck arrives
- → **Tag as "Fuel Truck"**
- Baggage loader arrives
- → **Tag as "Belt / Baggage"**
- Ground crew visible

**Late Phase (6-10min)**:
- Fueling/baggage complete
- Pushback tug connects
- → **Tag as "Tug / Pushback"**

**Tip**: Pause playback (Pause button) while tagging vehicles!

---

## 🎬 Demo Strategy

### For stakeholders/judges:

1. **Start with Demo Mode**: Show concept quickly
2. **Switch to Real Video**: "Now let's see it on real airport footage..."
3. **Tag 2-3 vehicles live**: Show human-in-the-loop
4. **Point out alerts**: Safety violations in engine zone
5. **Show sequence progress**: GPU → Fuel → Baggage → Pushback

---

## 📊 Expected Results (Real Frames)

### Detection Rates (estimated):
- Aircraft: **>95%** (large, clear object)
- Vehicles: **~90%** (trucks, cars)
- People: **~85%** (smaller, occlusions)

### Tracking:
- ID switches: **<5%** (with class-aware matching)
- Most vehicles keep consistent ID

### Tasks:
- With proper tagging: **All 4 tasks** should activate
- Sequence state machine should progress through all steps

---

## 💾 Export After Test

After running through video:

1. Click **"📊 Export JSON"**
   - Saves: `turnaround_run-0001_601s.json`
   - Contains: All alerts, tasks, sequence state

2. Click **"📄 Export CSV"**
   - Saves: `alerts_run-0001_601s.csv`
   - Analyze in Excel/Python

---

## ✅ Success Criteria

**Test passed if**:
- ✓ All 601 frames load
- ✓ Aircraft detected in >90% of frames
- ✓ At least 3 vehicles tracked with consistent IDs
- ✓ Asset tagging works (roles assigned)
- ✓ Tasks become ACTIVE when tagged vehicles in ROIs
- ✓ Sequence progresses: GPU → Fuel → Baggage → Pushback
- ✓ Alerts generated (safety violations if any)
- ✓ Export produces valid JSON/CSV

---

## 🔄 Switching Back to Short Version

If you need faster testing:

**In sidebar**: Change "Frames folder" to `data/frames_1fps`

Or edit [app.py:312](app.py:312):
```python
frames_folder = st.text_input("Frames folder", value="data/frames_1fps")
```

---

**Ready to test with full turnaround video! 🎥✈️**
