# Future Enhancements - GODC System

This document outlines planned enhancements for the Ground Operations Dispatcher Control (GODC) system.

## 1. Visual Learning System for Asset Recognition

### Problem Statement
Currently, asset tagging is stored as `track_id → role` mappings in `data/asset_roles.json`. This has limitations:
- **Track IDs change** between video runs or when tracker loses/re-acquires objects
- **Manual re-tagging required** for each new video session
- **No cross-video persistence** of learned asset identities

### Proposed Solution: Visual Feature-Based Asset Learning

Implement a lightweight machine learning system that learns to recognize assets based on visual and spatial features, enabling automatic tagging across different video runs.

#### Feature Extraction
For each tagged asset, extract and store:

1. **Visual Features:**
   - Bounding box dimensions (width, height, aspect ratio)
   - Average color histogram (HSV color space)
   - Relative size compared to aircraft/frame
   - Approximate shape/silhouette (contour features)

2. **Spatial Features:**
   - Typical operating zone (which ROIs the asset frequents)
   - Entry/exit patterns (where it typically appears first)
   - Parking position when idle

3. **Temporal Features:**
   - Typical duration in scene
   - Movement patterns (speed, trajectory)

#### Implementation Approach

```python
# Pseudo-code structure

@dataclass
class AssetProfile:
    role: str  # e.g., "FUEL_TRUCK"
    visual_features: Dict[str, float]  # {width_mean, height_mean, color_hist, ...}
    spatial_features: Dict[str, float]  # {roi_frequency, typical_position, ...}
    confidence: float  # Learning confidence (0-1)
    sample_count: int  # Number of observations

class VisualAssetLearner:
    def __init__(self):
        self.profiles: Dict[str, AssetProfile] = {}  # role -> profile

    def learn_from_tagged_asset(self, detection, role, roi):
        """Update asset profile based on tagged detection"""
        features = self._extract_features(detection, roi)
        if role in self.profiles:
            self._update_profile(role, features)
        else:
            self._create_profile(role, features)

    def predict_role(self, detection, roi) -> Tuple[str, float]:
        """Predict role for untagged detection"""
        features = self._extract_features(detection, roi)
        best_match = self._find_best_match(features)
        return best_match.role, best_match.confidence

    def auto_tag_frame(self, detections_df, rois, confidence_threshold=0.75):
        """Automatically tag detections based on learned profiles"""
        suggestions = {}
        for idx, detection in detections_df.iterrows():
            role, conf = self.predict_role(detection, rois)
            if conf >= confidence_threshold:
                suggestions[detection.track_id] = role
        return suggestions
```

#### Storage Format

```json
{
  "asset_profiles": {
    "FUEL_TRUCK": {
      "visual_features": {
        "width_mean": 120.5,
        "width_std": 8.2,
        "height_mean": 80.3,
        "height_std": 5.1,
        "aspect_ratio": 1.5,
        "color_hsv_hist": [0.2, 0.3, 0.5, ...],
        "relative_size": 0.08
      },
      "spatial_features": {
        "roi_frequency": {
          "fuel": 0.85,
          "aircraft": 0.10,
          "nose": 0.05
        },
        "typical_entry_zone": "fuel",
        "typical_position": [650, 350]
      },
      "confidence": 0.92,
      "sample_count": 145
    },
    "GPU_TRUCK": { ... },
    ...
  },
  "learning_metadata": {
    "last_updated": "2024-01-14T10:30:00Z",
    "total_frames_processed": 450,
    "model_version": "1.0"
  }
}
```

#### Integration Points

1. **During Manual Tagging** (in `render_asset_tagging()`):
   - When user tags an asset, call `learner.learn_from_tagged_asset()`
   - Incrementally improve profiles with each manual tag

2. **Auto-Tagging at Playback Start**:
   - When video starts, call `learner.auto_tag_frame()` for first N frames
   - Present suggestions to user for approval
   - High-confidence predictions can be auto-applied

3. **Continuous Learning**:
   - As playback continues, update profiles for tagged assets
   - Improve confidence scores over time
   - Detect when assets significantly deviate from profile (flag for review)

#### UI Enhancements

1. **Learning Dashboard:**
   - Show confidence levels for each asset role
   - Visualize learned features (color histograms, typical positions)
   - "Reset Learning" button to clear profiles

2. **Auto-Tag Suggestions:**
   - Show suggested tags with confidence scores
   - "Accept All High-Confidence" button
   - Quick approve/reject for each suggestion

3. **Profile Management:**
   - Export/import learned profiles
   - Share profiles across team members
   - Version control for profiles

#### Benefits

- ✅ **Cross-video persistence**: Assets recognized across different runs
- ✅ **Reduced manual tagging**: Auto-tag high-confidence detections
- ✅ **Improved accuracy**: Learn from user corrections
- ✅ **Faster onboarding**: New videos require minimal tagging
- ✅ **Transferable knowledge**: Export profiles for other airports/gates

#### Implementation Priority

**Phase 1: Foundation (2-3 days)**
- Implement feature extraction for vehicles
- Create `AssetProfile` data structure
- Implement basic similarity matching

**Phase 2: Learning Loop (2-3 days)**
- Integrate learning during manual tagging
- Implement profile persistence (JSON storage)
- Add confidence scoring

**Phase 3: Auto-Tagging (2-3 days)**
- Implement auto-tag predictions
- Add UI for suggestion approval
- Tune confidence thresholds

**Phase 4: Advanced Features (3-5 days)**
- Add temporal features (movement patterns)
- Implement transfer learning (fine-tune from base profiles)
- Add profile visualization dashboard

#### Technical Considerations

- **Lightweight approach**: Use simple statistical features, not deep learning
- **Fast inference**: Predictions must run in <10ms per detection
- **Incremental learning**: Update profiles without reprocessing all data
- **Robustness**: Handle lighting changes, different camera angles
- **Privacy**: Visual features should not identify people

#### Alternative: Simple Heuristics

For a minimal viable implementation, start with **position-based heuristics**:

```python
# Simple position-based recognition
ASSET_ZONES = {
    "FUEL_TRUCK": {"roi": "fuel", "typical_bbox": (620, 170, 130, 90)},
    "GPU_TRUCK": {"roi": "nose", "typical_bbox": (260, 250, 120, 80)},
    # ...
}

def suggest_role_by_position(detection, rois):
    for role, zone in ASSET_ZONES.items():
        if _in_roi(detection.bbox, rois[zone["roi"]]):
            bbox_similarity = _bbox_similarity(detection.bbox, zone["typical_bbox"])
            if bbox_similarity > 0.7:
                return role, bbox_similarity
    return None, 0.0
```

This provides immediate value with minimal implementation cost.

---

## 2. Other Future Enhancements

### 2.1 Multi-Camera Support
- Stitch multiple camera feeds
- Track assets across camera boundaries
- 360° aircraft coverage

### 2.2 Real-Time Alerts
- Push notifications for critical alerts
- SMS/email escalation for overdue tasks
- Audio alerts for safety violations

### 2.3 Historical Analytics
- Turnaround time trends
- Bottleneck identification
- Resource utilization reports

### 2.4 Integration with Airport Systems
- Flight information systems (FIDS)
- Ground handling management systems
- Fuel ordering systems

### 2.5 Mobile App
- Remote monitoring on tablets/phones
- Field operator checklist app
- Photo upload for incident reports

---

**Document Version**: 1.0
**Last Updated**: 2024-01-14
**Author**: GODC Development Team
