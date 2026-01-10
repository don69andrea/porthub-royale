# Turnaround Prozess ZRH

Hier ist der Prozess für den Prototypen:

```mermaid
graph LR
    %% --- STYLING ---
    classDef airport fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,rx:5,ry:5;
    classDef handler fill:#fff9c4,stroke:#fbc02d,stroke-width:2px,rx:5,ry:5;
    classDef airline fill:#ffebee,stroke:#c62828,stroke-width:2px,rx:5,ry:5;
    classDef thirdparty fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,rx:5,ry:5;
    classDef gateway fill:#ffffff,stroke:#333,stroke-width:2px,rotation:45;

    %% --- PHASE 1: START ---
    subgraph Pre [Phase 1: Ankunft]
        direction LR
        Start((Start<br>AIBT)):::handler --> Chocks[Chocks On<br>Sofort]:::handler
        Chocks --> GPU[GPU Connect<br>Max 5 Min]:::handler
        GPU --> PBB[PBB Connect<br>1-2 Min]:::airport
    end

    PBB --> Split{Parallel}

    %% --- PHASE 2: PARALLELE STRÖME ---
    %% Hier nutzen wir Cluster, damit die Ströme als "Bahnen" sichtbar sind
    
    subgraph Stream_Pax [Passagiere & Kabine]
        direction LR
        Deboard[Deboarding<br>10-15 Min]:::airline --> Clean[Cleaning<br>10-15 Min]:::thirdparty
        Clean --> Cater[Catering<br>10-15 Min]:::thirdparty
        Cater --> Board[Boarding<br>15-20 Min]:::airline
    end

    subgraph Stream_Wing [Logistik Below Wing]
        direction LR
        Unload[Unload Baggage<br>15-20 Min]:::handler --> Refuel[Refueling<br>15-20 Min]:::thirdparty
        Refuel --> Load[Load Baggage<br>20-30 Min]:::handler
    end

    subgraph Stream_Tech [Technik & Check]
        direction LR
        FOD[FOD Check<br>Pre-Arrival]:::handler --> Water[Water Service<br>Parallel]:::thirdparty
        Water --> Walk[Final Walkaround<br>5 Min]:::handler
    end

    %% Verbindungen vom Splitter zu den Start-Punkten der Ströme
    Split --> Deboard
    Split --> Unload
    Split --> FOD

    %% --- JOIN & PHASE 3 ---
    Board --> Join{Aircraft<br>Ready}
    Load --> Join
    Walk --> Join

    subgraph Out [Phase 3: Abflug]
        direction LR
        Join --> PBB_Out[PBB Disconnect<br>1-2 Min]:::airport
        PBB_Out --> GSE_Out[GSE Removal<br>Safety Line]:::handler
        GSE_Out --> Tug[Pushback Tug<br>Ready]:::handler
        Tug --> StartEng[Engine Start<br>TSAT]:::handler
        StartEng --> End((Ende<br>ATOT)):::handler
    end