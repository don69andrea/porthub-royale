# Fingerdock & Movement Detection — Complete Refactor

**Date**: January 10, 2026
**Commits**: `8fe7e54`, `fbfe3ee`
**Status**: ✅ **READY FOR TESTING**

---

## 🎯 Problems Fixed (Based on Screenshot Analysis)

### 1. ❌ Incorrect Passenger ROI
**Problem**: Passenger door ROI was at wrong location (far right, outside fingerdock)
**Solution**: Moved ROI to fingerdock area (red circle in screenshot)
- **Old**: `980,200,1180,500`
- **New**: `850,180,1050,480`

---

### 2. ❌ No Movement Direction Detection
**Problem**: System couldn't distinguish between:
- Deboarding (passengers moving left→right)
- Boarding (passengers moving right→left)
- Ground staff (right→left BEFORE deboarding)

**Solution**: Implemented position tracking over time
- Tracks each person's x-coordinate across frames
- Calculates average movement direction
- **Left→Right** = Deboarding
- **Right→Left** = Boarding OR Ground Staff Prep

---

### 3. ❌ Premature Task Completion
**Problem**: Tasks marked as DONE immediately when first person detected
**Example**: "Passenger Deboarding DONE" after just 5 seconds

**Solution**: New state progression
- `NOT_STARTED` → First detection
- **`STARTED`** → First passenger detected (NEW STATE)
- **`ONGOING`** → Continuous activity (NEW STATE)
- `DONE` → No activity for 50+ seconds (Deboarding) or 10+ seconds (Boarding)

**Timing Rules**:
| Task | Start | Ongoing | Done Trigger |
|------|-------|---------|--------------|
| **Deboarding** | First L→R person | L→R activity continues | No L→R for 50s |
| **Boarding** | Sustained R←L (10s) | R←L activity continues | Completes naturally |
| **Ground Staff Prep** | R←L before deboarding | R←L activity | No R←L for 10s |

---

### 4. ❌ False Boarding Detection
**Problem**: Ground staff walking R←L mistaken for passenger boarding
**Example**: "Passenger Boarding DONE" before deboarding even started

**Solution**: Sequence logic enforcement
- **Boarding can ONLY start AFTER deboarding is DONE**
- R←L movement BEFORE deboarding = "Ground Staff Preparation"
- R←L movement AFTER deboarding = "Passenger Boarding"

---

### 5. ❌ No Fingerdock Detection
**Problem**: No tracking of fingerdock position/movement/connection status

**Solution**: New fingerdock_detection.py module
- Detects large objects (truck/bus/train) in fingerdock ROI
- Tracks position changes to detect movement
- **States**:
  - `NOT_CONNECTED`: Fingerdock visible but not at aircraft
  - `OPERATING`: Fingerdock moving/extending (gray cover deploying)
  - `CONNECTED`: Fingerdock stationary at aircraft 10+ seconds

---

### 6. ❌ Dashboard Flickering
**Problem**: "Active Tasks" card rapidly appearing/disappearing (milliseconds)
**Cause**: Tasks transitioning between ACTIVE↔INACTIVE too quickly

**Solution**: 5-second stability delay
- Dashboard caches current display state
- State changes only apply after 5 seconds
- Prevents visual flickering
- Acts like a "relay" with time delay

---

### 7. ❌ Live Video Jumping
**Problem**: Video shifts up/down when dashboard content changes
**Cause**: Dashboard cards changing height when tasks appear/disappear

**Solution**: Fixed-height CSS
- All cards have `min-height: 62px`
- Flexbox centering keeps content vertically centered
- Video position remains constant

---

### 8. ❌ Too Many Emojis
**Problem**: User requested removal of all emojis from dashboard

**Solution**: Removed emojis from all dashboard cards
- "ACTIVE TASKS" (was "🔵 ACTIVE TASKS")
- "SAFETY" (was "🚨⚠️✅ SAFETY")
- "AIRSIDE" (was "👥 AIRSIDE")
- "PROGRESS" (was "📊 PROGRESS")

---

## 📁 New Files Created

### 1. [src/passenger_flow.py](src/passenger_flow.py)
**Purpose**: Movement direction detection for passenger flow

**Key Components**:
```python
@dataclass
class PassengerFlowState:
    person_positions: Dict[int, List[Tuple[float, float, float]]]  # track_id → [(x, y, t), ...]
    left_to_right_count: int  # Deboarding
    right_to_left_count: int  # Boarding / Ground staff
    last_deboarding_detection: Optional[float]
    last_boarding_detection: Optional[float]
    last_ground_staff_detection: Optional[float]
    deboarding_started: bool
    boarding_started: bool
    ground_staff_prep_done: bool
```

**Algorithm**:
1. Track centroid position of each person over last 10 frames
2. Calculate x-coordinate changes between frames
3. Average change > +5 pixels/frame → Left-to-right (Deboarding)
4. Average change < -5 pixels/frame → Right-to-left (Boarding/Ground staff)
5. Apply sequence logic to distinguish boarding from ground staff

**Function Signature**:
```python
def update_passenger_flow(
    dets_df: pd.DataFrame,
    roi_passenger_door: Optional[Tuple[int, int, int, int]],
    t_sec: float,
    state: PassengerFlowState,
) -> Dict[str, str]:
    """
    Returns:
    {
        "ground_staff_prep": "NOT_STARTED" | "ONGOING" | "DONE",
        "passenger_deboarding": "NOT_STARTED" | "STARTED" | "ONGOING" | "DONE",
        "passenger_boarding": "NOT_STARTED" | "STARTED" | "ONGOING" | "DONE",
    }
    """
```

---

### 2. [src/fingerdock_detection.py](src/fingerdock_detection.py)
**Purpose**: Detect fingerdock position and connection status

**Key Components**:
```python
@dataclass
class FingerdockState:
    status: str  # NOT_CONNECTED | OPERATING | CONNECTED
    first_detected: Optional[float]
    connected_at: Optional[float]
    last_position: Optional[Tuple[float, float]]
    last_position_time: Optional[float]
    is_moving: bool
```

**Algorithm**:
1. Look for large objects (truck/bus/train) in fingerdock ROI
2. Track centroid position across frames
3. Movement > 15 pixels → `OPERATING` (dock moving)
4. Stationary 10+ seconds after movement → `CONNECTED`
5. Stationary 15+ seconds from first detection → `CONNECTED` (already docked)

**Function Signature**:
```python
def detect_fingerdock(
    dets_df: pd.DataFrame,
    roi_fingerdock: Optional[Tuple[int, int, int, int]],
    t_sec: float,
    state: FingerdockState,
) -> str:
    """
    Returns: "NOT_CONNECTED" | "OPERATING" | "CONNECTED"
    """
```

---

## 🔄 Modified Files

### 1. [config/settings.yaml](config/settings.yaml)
**Changes**:
```yaml
# Updated passenger door ROI (moved to fingerdock location)
passenger_door:
  coordinates: [850, 180, 1050, 480]  # Was [980, 200, 1180, 500]
  description: "Passenger boarding/deboarding at fingerdock (gray cover area)"

# New fingerdock ROI
fingerdock:
  coordinates: [820, 160, 1080, 500]
  description: "Fingerdock structure (detection of dock position and cover)"
```

---

### 2. [app.py](app.py)
**Major Changes**:

#### A. State Initialization (Lines 99-115)
```python
# Dashboard stability (prevent flickering)
if "dashboard_state" not in st.session_state:
    st.session_state.dashboard_state = {
        "active_tasks_display": "",
        "active_tasks_last_change": 0.0,
        "stability_delay": 5.0,  # 5-second minimum display time
    }

# Passenger flow state
if "passenger_flow_state" not in st.session_state:
    from src.passenger_flow import PassengerFlowState
    st.session_state.passenger_flow_state = PassengerFlowState()

# Fingerdock state
if "fingerdock_state" not in st.session_state:
    from src.fingerdock_detection import FingerdockState
    st.session_state.fingerdock_state = FingerdockState()
```

#### B. ROI Inputs (Lines 384-385)
```python
roi_passenger_door = st.text_input("passenger_door (fingerdock)", value="850,180,1050,480")
roi_fingerdock = st.text_input("fingerdock", value="820,160,1080,500")
```

#### C. Passenger Flow & Fingerdock Detection (Lines 457-509)
```python
# Detect passenger movement direction and flow status
passenger_flow_status = update_passenger_flow(
    dets_df=dets_df,
    roi_passenger_door=rois.get("passenger_door"),
    t_sec=t_sec,
    state=st.session_state.passenger_flow_state,
)

# Update task_hist with passenger flow statuses
for task_key, status in passenger_flow_status.items():
    # ... (update task_hist logic)

# Detect fingerdock position
fingerdock_status = detect_fingerdock(
    dets_df=dets_df,
    roi_fingerdock=rois.get("fingerdock"),
    t_sec=t_sec,
    state=st.session_state.fingerdock_state,
)

# Update task_hist with fingerdock status
# ... (update task_hist logic)
```

#### D. Dashboard with Flickering Fix (Lines 491-584)
```python
# STABILITY: Prevent flickering with 5-second minimum display time
current_active_str = ""
if active_tasks:
    current_active_str = ", ".join(active_tasks[:2])
    if len(active_tasks) > 2:
        current_active_str += f" +{len(active_tasks)-2}"

dash_state = st.session_state.dashboard_state
time_since_change = t_sec - dash_state["active_tasks_last_change"]

if current_active_str != dash_state["active_tasks_display"]:
    # State changed
    if time_since_change >= dash_state["stability_delay"]:
        # Enough time passed, allow change
        dash_state["active_tasks_display"] = current_active_str
        dash_state["active_tasks_last_change"] = t_sec
# else: use cached display value for stability

display_active_str = dash_state["active_tasks_display"]

# Dashboard cards with min-height: 62px for layout stability
# NO EMOJIS
```

---

## 🧪 How to Test

### 1. Start Application
```bash
streamlit run app.py
```

### 2. Load Real Frames
- Uncheck "Demo Mode"
- Ensure frames folder contains your airport video frames
- Click "Play"

### 3. Watch for Passenger Movement

#### Expected Behavior (Deboarding):
**Scenario**: People walking left→right at fingerdock (exiting aircraft)

| Time | Event | Task Status | Dashboard |
|------|-------|-------------|-----------|
| t=0-5s | No movement | `NOT_STARTED` | "STANDBY" |
| t=5s | First person detected L→R | **`STARTED`** | "ACTIVE TASKS - Passenger Deboarding" |
| t=5-60s | Continuous L→R flow | **`ONGOING`** | "ACTIVE TASKS - Passenger Deboarding" (stable for 5s) |
| t=60s | Last person exits | Still `ONGOING` | Still showing (cached) |
| t=110s | No activity for 50s | **`DONE`** | "STANDBY" (after 5s delay) |

#### Expected Behavior (Ground Staff Prep):
**Scenario**: 2 staff members walking right→left at fingerdock BEFORE any passengers deboarded

| Time | Event | Task Status | Dashboard |
|------|-------|-------------|-----------|
| t=0-2s | No movement | `NOT_STARTED` | "STANDBY" |
| t=2s | Staff person detected R←L | **`ONGOING`** | "ACTIVE TASKS - Ground Staff Prep" |
| t=2-8s | Staff walking R←L | `ONGOING` | Showing task (stable) |
| t=12s | No R←L for 10s | **`DONE`** | "STANDBY" |
| **Later:** Deboarding starts | People now moving L→R | Deboarding `STARTED` | "ACTIVE TASKS - Passenger Deboarding" |

**Key**: R←L movement BEFORE deboarding = Ground staff, not boarding

#### Expected Behavior (Boarding):
**Scenario**: Passengers boarding AFTER deboarding completed

| Time | Event | Task Status | Constraint |
|------|-------|-------------|------------|
| ... | Deboarding completes (50s timeout) | Deboarding `DONE` | ✅ Prerequisite met |
| t=150s | First R←L person appears | Still `NOT_STARTED` | Need 10s sustained |
| t=150-160s | Continuous R←L flow | Still `NOT_STARTED` | Accumulating time |
| t=160s | 10s sustained R←L | **`STARTED`** | ✅ Boarding confirmed |
| t=160-200s | R←L continues | **`ONGOING`** | Active boarding |

**Key**: Boarding requires deboarding DONE + 10s sustained R←L movement

### 4. Watch for Fingerdock Movement

#### Expected Behavior:
| Time | Visual | Fingerdock Status | Dashboard |
|------|--------|-------------------|-----------|
| t=0-10s | Dock visible, not at aircraft | `NOT_CONNECTED` | (Not in active tasks) |
| t=10-30s | Dock extending, gray cover moving | **`OPERATING`** | "ACTIVE TASKS - Fingerdock" |
| t=40s | Dock stationary 10s | **`CONNECTED`** | Task complete |

**Detection Method**:
- System looks for large object (truck/bus/train class) in fingerdock ROI
- Tracks position changes
- Movement > 15 pixels → `OPERATING`
- Stationary 10+ seconds → `CONNECTED`

---

### 5. Check Dashboard Stability

**Test**: Rapidly toggle a task on/off

**Expected**:
- Dashboard does NOT flicker
- "Active Tasks" card shows state for minimum 5 seconds
- Video does NOT jump up/down
- All cards maintain min-height: 62px

**How to verify**:
1. Run app in Demo Mode
2. Watch dashboard between t=10-120s (GPU task appearing/disappearing)
3. Card should stay visible for 5+ seconds even if underlying state changes
4. Video position should remain constant

---

## 📊 New Task States Available

### Tasks in task_hist:
```python
{
    "ground_staff_prep": {
        "status": "NOT_STARTED" | "ONGOING" | "DONE",
        "since": <timestamp>,
        "last_seen": <timestamp>
    },
    "passenger_deboarding": {
        "status": "NOT_STARTED" | "STARTED" | "ONGOING" | "DONE",
        "since": <timestamp>,
        "last_seen": <timestamp>
    },
    "passenger_boarding": {
        "status": "NOT_STARTED" | "STARTED" | "ONGOING" | "DONE",
        "since": <timestamp>,
        "last_seen": <timestamp>
    },
    "fingerdock": {
        "status": "NOT_CONNECTED" | "OPERATING" | "CONNECTED",
        "since": <timestamp>,
        "last_seen": <timestamp>
    }
}
```

### Dashboard Display Logic:
```python
# Tasks shown as "active" in dashboard:
if status in ["ACTIVE", "STARTED", "ONGOING", "OPERATING"]:
    # Show in Active Tasks card
```

**Examples**:
- `STARTED` → "ACTIVE TASKS - Passenger Deboarding"
- `ONGOING` → "ACTIVE TASKS - Passenger Deboarding"
- `OPERATING` → "ACTIVE TASKS - Fingerdock"

---

## 🔧 Configuration Options

### Adjust Movement Detection Sensitivity
**File**: [src/passenger_flow.py:132](src/passenger_flow.py#L132)
```python
# Threshold for significant movement (adjust based on frame rate)
threshold = 5.0  # pixels per frame
```

**Change if**:
- Too sensitive (detects small movements as direction changes)
- Not sensitive enough (misses actual movement)

### Adjust Timing Thresholds
**File**: [src/passenger_flow.py:24-25](src/passenger_flow.py#L24-L25)
```python
deboarding_timeout: float = 50.0  # No movement for 50s → DONE
boarding_timeout: float = 10.0    # Movement for 10s → START
```

**Deboarding timeout (50s)**:
- Increase if passengers take longer to fully exit
- Decrease for faster deboarding processes

**Boarding timeout (10s)**:
- Increase to require more sustained R←L movement before confirming boarding
- Decrease to detect boarding start faster

### Adjust Fingerdock Movement Threshold
**File**: [src/fingerdock_detection.py:69](src/fingerdock_detection.py#L69)
```python
# Movement threshold (pixels)
movement_threshold = 15.0
```

**Change if**:
- Fingerdock moves slowly (decrease threshold)
- Small camera shakes cause false "OPERATING" state (increase threshold)

### Adjust Dashboard Stability Delay
**File**: [app.py:104](app.py#L104)
```python
"stability_delay": 5.0,  # Show state for minimum 5 seconds
```

**Change if**:
- 5 seconds too long (users want faster updates) → Decrease
- Still seeing flickering → Increase

---

## 🚀 Future Terminal Integration

**Note in code comments**:
> "In the future, a terminal software interface will be integrated, which will provide reliable information about passenger flow states (deboarding/boarding start/end times). This movement detection is a **heuristic fallback** for when terminal data is unavailable."

**When terminal API available**:
1. Replace `update_passenger_flow()` with API calls
2. Terminal provides:
   - Deboarding start time (aircraft door opened)
   - Deboarding end time (last passenger exited)
   - Boarding start time (first passenger entered)
   - Boarding end time (door closed)
3. Movement detection becomes validation/cross-check only

---

## 📝 Summary of All Changes

### ✅ Problems Fixed:
1. Passenger ROI corrected to fingerdock location
2. Movement direction detection implemented (L→R vs R←L)
3. Task states now use STARTED/ONGOING instead of instant DONE
4. Ground staff preparation distinguished from passenger boarding
5. Fingerdock position/movement tracking added
6. Dashboard flickering eliminated with 5-second stability delay
7. Live video layout shifts prevented with fixed-height CSS
8. All emojis removed from dashboard

### 📦 New Modules:
- `src/passenger_flow.py` (220 lines)
- `src/fingerdock_detection.py` (130 lines)

### 🔧 Modified Files:
- `config/settings.yaml` (passenger_door + fingerdock ROIs)
- `app.py` (dashboard stability, module integration, emoji removal)

### 📄 Documentation:
- `PASSENGER_FLOW_UPDATE.md` (original passenger flow documentation)
- `FINGERDOCK_FIXES_SUMMARY.md` (this file)

### 🎯 Commits:
- `8fe7e54` - Major refactor with new modules
- `fbfe3ee` - Integration into main pipeline

---

## 🎉 Ready for Demo!

**All requested features implemented**:
- ✅ Fingerdock ROI at correct location
- ✅ Movement direction detection
- ✅ Ground staff vs passenger boarding distinction
- ✅ Proper task state progression (STARTED → ONGOING → DONE)
- ✅ Fingerdock position tracking (NOT_CONNECTED → OPERATING → CONNECTED)
- ✅ Dashboard stability (no more flickering)
- ✅ Fixed video layout (no more jumping)
- ✅ No emojis

**Test with real frames and verify all behaviors!** 🚀

---

**Next Steps**:
1. Run with real airport video frames
2. Verify ROI alignment with fingerdock in footage
3. Observe passenger movement detection L→R and R←L
4. Check dashboard stability over time
5. Adjust thresholds if needed (see Configuration Options above)
6. Take screenshots for poster/presentation

**Demo Fair Ready!** ✨
