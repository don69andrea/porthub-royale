from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from rdflib import Graph, Namespace, RDF, RDFS, Literal, XSD, URIRef


EX = Namespace("http://example.org/porthub/")


@dataclass
class StepStatus:
    label: str
    done: bool
    t_sec: Optional[float] = None


def load_process_graph(ttl_path: str | Path) -> Graph:
    g = Graph()
    g.parse(str(ttl_path), format="turtle")
    return g


def extract_step_labels(g: Graph) -> List[str]:
    # The provided TTL uses rdfs:label for steps.
    labels = sorted({str(o) for o in g.objects(None, RDFS.label) if isinstance(o, Literal)})
    # Filter out generic headings:
    drop = {"Turnaround Process", "Turnaround Process - Process", "Aircraft", "Ramp Team"}
    return [l for l in labels if l not in drop]


def build_runtime_graph(process_ttl: str | Path, run_id: str, detected_events: Dict[str, float]) -> tuple[Graph, List[StepStatus]]:
    """
    Minimal hybrid bit:
    - Load the symbolic process model (BPMN TTL)
    - Create a run graph with instances for completed steps
    - Return statuses for the UI
    `detected_events` maps step_label -> t_sec
    """
    base = load_process_graph(process_ttl)
    run = Graph()
    run.bind("ex", EX)

    run_uri = EX[f"run/{run_id}"]
    run.add((run_uri, RDF.type, EX.TurnaroundRun))
    run.add((run_uri, EX.createdAt, Literal(run_id, datatype=XSD.string)))

    step_labels = extract_step_labels(base)
    statuses: List[StepStatus] = []

    for label in step_labels:
        done = label in detected_events
        t = detected_events.get(label)
        statuses.append(StepStatus(label=label, done=done, t_sec=t))

        if done:
            step_uri = EX[f"step/{run_id}/{_slug(label)}"]
            run.add((step_uri, RDF.type, EX.CompletedStep))
            run.add((step_uri, RDFS.label, Literal(label)))
            run.add((step_uri, EX.atSecond, Literal(float(t), datatype=XSD.double)))
            run.add((run_uri, EX.hasStep, step_uri))

    return run, statuses


def _slug(s: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in s).strip("-")
