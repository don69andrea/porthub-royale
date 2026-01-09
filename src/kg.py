# src/kg.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Any


@dataclass
class KGNode:
    id: str
    type: str
    props: Dict[str, Any]


@dataclass
class KGEdge:
    src: str
    dst: str
    rel: str
    props: Dict[str, Any]


class SimpleKG:
    def __init__(self):
        self.nodes: Dict[str, KGNode] = {}
        self.edges: List[KGEdge] = []

    def upsert_node(self, node_id: str, node_type: str, **props: Any):
        if node_id in self.nodes:
            self.nodes[node_id].props.update(props)
        else:
            self.nodes[node_id] = KGNode(id=node_id, type=node_type, props=dict(props))

    def add_edge(self, src: str, dst: str, rel: str, **props: Any):
        self.edges.append(KGEdge(src=src, dst=dst, rel=rel, props=dict(props)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [dict(id=n.id, type=n.type, props=n.props) for n in self.nodes.values()],
            "edges": [dict(src=e.src, dst=e.dst, rel=e.rel, props=e.props) for e in self.edges],
        }
# ----------------------------