from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

@dataclass
class PerceptionArtifact:
    """Structured evidence returned by a modality worker (HAOS v1.1 Emenda 18).

    The parent agent receives structured perception — never a raw mini-chat —
    so a text-only brain can reason about vision/audio/video results without
    switching models and without losing provenance (who produced it, with
    which model, and how certain the reading is).
    """

    artifact_id: str
    modality: str  # image | audio | video | pdf | document
    summary: str
    source_uri: str = ""
    observations: List[str] = field(default_factory=list)
    extracted_text: str = ""
    interpretation: str = ""
    uncertainty: str = ""  # e.g. "low | medium | high"
    structured_data: Optional[Dict[str, Any]] = None
    produced_by: str = ""  # worker/provider id
    model_identity: Optional[Dict[str, str]] = None  # {family, variant, revision}

class VisionWorker:
    """Modality Worker for image understanding without changing main agent model."""

    provider_id = "vision-worker"

    def __init__(self, produced_by: str = "vision-worker",
                 model_identity: Optional[Dict[str, str]] = None):
        self.produced_by = produced_by
        self.model_identity = model_identity or {"family": "vision", "variant": "default"}

    def analyze_image(self, image_uri: str) -> PerceptionArtifact:
        return PerceptionArtifact(
            artifact_id="art-vis-001",
            modality="image",
            source_uri=image_uri,
            summary="Architecture Diagram showing Protocol Fabric, Task Engine and Memory Fabric.",
            observations=["Diagram contains layered boxes", "Labels reference MCP/ACP/ANP"],
            extracted_text="ANP 1.1 DID Identity -> Authentication -> Envelope Transport",
            interpretation="Layered architecture: protocol adapters above an internal bus.",
            uncertainty="medium",
            structured_data={"nodes": ["ANP", "TaskEngine", "MemoryFabric"], "edges": 2},
            produced_by=self.produced_by,
            model_identity=self.model_identity
        )
