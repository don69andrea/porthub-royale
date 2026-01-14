# Parameter Optimization - 2026-01-14

## Übersicht der optimierten Parameter

Diese Datei dokumentiert die Optimierungen an den System-Parametern für stabileres Tracking und bessere Task-Erkennung.

---

## 1. YOLO Detection Confidence

**Datei:** `app.py:630`

| Parameter | Vorher | Nachher | Grund |
|-----------|--------|---------|-------|
| `conf` (default) | 0.20 | **0.40** | Reduziert False Positives drastisch |

### Auswirkungen:
- ✅ **Weniger "flackernde" Detections** → stabilere Track IDs
- ✅ **Weniger Fehlerkennungen** → GPU/Baggage Handler werden zuverlässiger erkannt
- ✅ **Bessere Tracking-Qualität** → weniger ID-Swaps zwischen Personen/Fahrzeugen
- ⚠️ User kann Confidence weiterhin über Slider anpassen (0.05-0.95)

### Technischer Hintergrund:
Bei 20% Confidence werden viele unsichere Detections akzeptiert:
- Schatten können als Personen erkannt werden
- Teile von Fahrzeugen werden als separate Objekte erkannt
- Reflektionen im Glas führen zu Phantom-Detections

40% ist der Standard für production-grade Computer Vision Systeme.

---

## 2. Tracker max_missed Frames

**Datei:** `src/infer.py:56`

| Parameter | Vorher | Nachher | Grund |
|-----------|--------|---------|-------|
| `max_missed` | 10 frames | **6 frames** | Verhindert Track ID Wiederverwendung |

### Auswirkungen:
- ✅ **Schnelleres Freigeben von Track IDs** → weniger Verwechslungen
- ✅ **Löst das STAIRS/Person Problem** → alte IDs werden nicht mehr auf neue Objekte übertragen
- ✅ **Bessere Reaktivität** → verschwundene Objekte werden schneller erkannt
- ℹ️ Bei 4 FPS Playback: 6 frames = 1.5 Sekunden Puffer (ausreichend)

### Technischer Hintergrund:
Der SimpleIoUTracker behält Tracks für `max_missed` Frames auch wenn sie nicht mehr sichtbar sind:
- **Zu hoch (10 frames):** Track ID bleibt 2.5 Sekunden bestehen → kann auf neues Objekt übertragen werden
- **Optimal (6 frames):** 1.5 Sekunden Puffer für temporäre Verdeckungen → dann wird ID freigegeben

**Das STAIRS/Person Problem:**
- Track ID 64324 war ursprünglich stairs (vehicle)
- Stairs verschwand, aber Track blieb 10 frames aktiv
- Neue Person erschien und bekam die gleiche Track ID
- Person erbte das "STAIRS" Tag → ERROR!

Mit 6 frames wird die ID schneller freigegeben.

---

## 3. Sequence DONE Sensitivity

**Datei:** `app.py:113`

| Parameter | Vorher | Nachher | Grund |
|-----------|--------|---------|-------|
| `seq_done_sensitivity` | 6.0 sec | **3.5 sec** | Schnellere Task completion detection |

### Auswirkungen:
- ✅ **Tasks werden schneller als DONE markiert** → realistischere Timeline
- ✅ **Besseres User-Feedback** → sofortige Bestätigung wenn Task abgeschlossen
- ✅ **Näher an realen Airport Operations** → typische Task-Dauer ist kürzer
- ⚠️ User kann weiterhin über Slider anpassen (1.0-20.0 sec)

### Technischer Hintergrund:
Ein Task wird als DONE markiert wenn:
1. Er war mindestens `min_active_sec_for_done` Sekunden ACTIVE
2. Danach Status wechselt zu INACTIVE

**Beispiel GPU Connection:**
- GPU Operator verbindet Kabel (t=0)
- Task wird ACTIVE (sofort)
- Nach 3.5 Sekunden: ausreichend lange aktiv
- Operator geht weg → Task wird INACTIVE
- System markiert Task als DONE ✓

Vorher (6 sec): Operator musste 6 Sekunden bleiben → unrealistisch.

---

## 4. Role Handoff max_age

**Datei:** `app.py:225`

| Parameter | Vorher | Nachher | Grund |
|-----------|--------|---------|-------|
| `max_age_sec` | 8.0 sec | **5.0 sec** | Weniger falsche Role-Übertragungen |

### Auswirkungen:
- ✅ **Role Memory bleibt kürzer aktiv** → weniger Verwechslungen
- ✅ **Präzisere Role-Übertragung** → nur bei wirklich gleichen Objekten
- ✅ **Weniger False Handoffs** → verhindert dass Role auf falsches Objekt springt
- ℹ️ 5 Sekunden sind immer noch ausreichend für Track ID Recovery

### Technischer Hintergrund:
Role Handoff System:
1. Wenn getaggtes Objekt verschwindet (Track ID verloren)
2. System merkt sich letzte bbox Position für 8 Sekunden
3. Wenn neues Objekt mit ähnlicher Position erscheint → Role wird übertragen

**Problem mit 8 Sekunden:**
- GPU Truck fährt weg (t=0)
- 7 Sekunden später erscheint Baggage Loader an ähnlicher Position (t=7)
- System überträgt "GPU_TRUCK" Role auf Baggage Loader → FEHLER!

Mit 5 Sekunden: Zeitfenster ist enger → weniger falsche Übertragungen.

---

## 5. Unveränderte Parameter (bereits optimal)

Diese Parameter wurden analysiert und als optimal befunden:

### YOLO IoU (NMS)
- **Wert:** 0.50
- **Grund:** Standard für Non-Maximum Suppression, funktioniert gut

### Tracker IoU Match
- **Wert:** 0.35
- **Grund:** Gute Balance zwischen Tracking-Stabilität und Genauigkeit

### Role Handoff IoU Threshold
- **Wert:** 0.20
- **Grund:** Großzügig genug für Role-Recovery, aber nicht zu permissiv

### Sequence Deadlines
- **passenger_unboarding:** 180 sec (3 min)
- **gpu:** 120 sec (2 min)
- **Grund:** Realistisch für Airport Turnaround Operations

### Validation Thresholds
- Alle Validation Rules sind sinnvoll konfiguriert

---

## Testing & Validation

Nach den Änderungen sollten folgende Verbesserungen sichtbar sein:

### 1. Stabileres Tracking
- Weniger "flickering" bei Personen-Detections
- Track IDs bleiben länger konstant für gleiche Objekte
- Weniger falsche ID-Swaps

### 2. Bessere Task-Erkennung
- GPU connection wird zuverlässiger erkannt
- Baggage handling triggert korrekter
- Passenger flow ist stabiler

### 3. Keine Role-Mismatches mehr
- Personen bekommen keine Vehicle-Roles mehr
- Vehicles bekommen keine Person-Roles mehr
- Track ID Wiederverwendung verursacht keine Probleme

### 4. Schnelleres Feedback
- Tasks werden schneller als DONE markiert
- Sequence progression fühlt sich natürlicher an
- Näher an realer Airport Timeline

---

## Troubleshooting

### Wenn zu viele Detections fehlen:
→ Confidence im Sidebar-Slider reduzieren (z.B. auf 0.30)

### Wenn Tasks zu schnell als DONE markiert werden:
→ "DONE sensitivity" Slider erhöhen (z.B. auf 5.0 sec)

### Wenn Track IDs zu oft wechseln:
→ max_missed könnte wieder auf 7-8 erhöht werden (Code ändern erforderlich)

### Wenn Role Handoff nicht funktioniert:
→ max_age_sec könnte auf 6.0 erhöht werden (Code ändern erforderlich)

---

## Weitere mögliche Optimierungen (Zukunft)

1. **Adaptive Confidence:** Confidence dynamisch anpassen je nach ROI (höher in kritischen Zonen)
2. **Class-specific Tracking:** Separate Tracker für Persons vs Vehicles
3. **Multi-Model Ensemble:** Kombiniere mehrere YOLO-Modelle für robustere Detections
4. **Temporal Smoothing:** Task status über mehrere Frames glätten
5. **ROI-based Filtering:** Nur relevante Detections in Task-ROIs berücksichtigen

---

## Changelog

### 2026-01-14 - Initial Optimization
- ✅ YOLO Confidence: 0.20 → 0.40
- ✅ Tracker max_missed: 10 → 6
- ✅ Sequence DONE: 6.0 → 3.5 sec
- ✅ Role Handoff max_age: 8.0 → 5.0 sec
