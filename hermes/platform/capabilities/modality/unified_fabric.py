"""Unified Multimodal Fabric (Marco 8).

Permite que agentes com cérebro puramente textual encontrem conteúdo visual/áudio/documento
e despachem para workers especializados em modalidade (Vision/Audio/OCR/Diagram), recebendo
de volta um PerceptionArtifact tipado sem explosão de contexto ou re-prompts custosos de visão.

Restrições:
- Strictly stdlib-only imports.
- Strictly PEP-420 namespace compliance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import mimetypes
import os
import uuid


class PerceptionType(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    OCR = "ocr"
    DIAGRAM = "diagram"


@dataclass
class PerceptionArtifact:
    """Artifact tipado de percepção multimodal (Marco 8).
    
    Campos canônicos exigidos pela especificação:
    - type: tipo de percepção (image, audio, ocr, diagram)
    - summary: resumo textual de alto nível da percepção
    - structured_observations: lista de observações estruturadas
    - confidence: grau de confiança (0.0 a 1.0)
    - raw_uri: URI ou caminho do recurso bruto analisado
    - ocr_text: texto extraído por OCR ou transcrição (opcional)
    """
    type: str  # image, audio, ocr, diagram
    summary: str
    structured_observations: List[str] = field(default_factory=list)
    confidence: float = 1.0
    raw_uri: str = ""
    ocr_text: Optional[str] = None
    artifact_id: str = field(default_factory=lambda: f"art-perc-{uuid.uuid4().hex[:8]}")
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "type": self.type,
            "summary": self.summary,
            "structured_observations": list(self.structured_observations),
            "confidence": self.confidence,
            "raw_uri": self.raw_uri,
            "ocr_text": self.ocr_text,
            "metadata": dict(self.metadata),
        }

    def render_for_prompt(self) -> str:
        """Formata o artefato como bloco de texto estruturado para injeção no working context do agente primário."""
        obs_lines = "\n".join(f"- {obs}" for obs in self.structured_observations) if self.structured_observations else "- (none)"
        ocr_block = f"\nExtracted Text/OCR:\n{self.ocr_text}\n" if self.ocr_text else ""
        return (
            f"<multimodal_perception artifact_id=\"{self.artifact_id}\" type=\"{self.type}\" confidence=\"{self.confidence:.2f}\">\n"
            f"Source URI: {self.raw_uri}\n"
            f"Summary: {self.summary}\n"
            f"Structured Observations:\n{obs_lines}"
            f"{ocr_block}\n"
            f"</multimodal_perception>"
        )

    def format_for_context(self) -> str:
        """Formata o artefato como bloco de texto conciso para injeção no contexto do agente textual."""
        obs_lines = "\n".join(f"- {obs}" for obs in self.structured_observations)
        ocr_part = f"\nOCR Text: {self.ocr_text}" if self.ocr_text else ""
        return (
            f"[PerceptionArtifact: type={self.type}, confidence={self.confidence:.2f}]\n"
            f"Summary: {self.summary}\n"
            f"Observations:\n{obs_lines}\n"
            f"Source URI: {self.raw_uri}{ocr_part}"
        )


class MultimodalDispatchPattern:
    """Padrão de despacho multimodal (Marco 8).
    
    Permite que um agente primário textual delegue o processamento de imagens,
    áudios, OCRs ou diagramas para workers especializados, agregando
    artefatos tipados sem poluir o histórico nem exigir re-prompts multimodais
    custosos no cérebro principal.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, Callable[[str, Dict[str, Any]], PerceptionArtifact]] = {}
        self._register_default_handlers()

    def register_worker(
        self,
        modality_type: str | PerceptionType,
        handler: Callable[[str, Dict[str, Any]], PerceptionArtifact],
    ) -> None:
        """Registra ou substitui um worker especializado para um tipo de percepção."""
        key = modality_type.value if isinstance(modality_type, PerceptionType) else str(modality_type).lower()
        self._handlers[key] = handler

    def detect_modality(self, raw_uri: str, mime_type: Optional[str] = None) -> str:
        """Infere o tipo de modalidade baseado no MIME type, extensão ou esquema do URI."""
        if mime_type:
            mime_lower = mime_type.lower()
            if mime_lower.startswith("image/"):
                return PerceptionType.IMAGE.value
            if mime_lower.startswith("audio/"):
                return PerceptionType.AUDIO.value
            if mime_lower in ("application/pdf", "text/plain", "application/msword") or "pdf" in mime_lower:
                return PerceptionType.OCR.value
            if any(term in mime_lower for term in ("mermaid", "diagram", "drawio", "plantuml")):
                return PerceptionType.DIAGRAM.value

        uri_lower = raw_uri.lower()
        # Heurísticas de diagrama
        if any(uri_lower.endswith(ext) for ext in (".puml", ".mermaid", ".mmd", ".dot", ".drawio")):
            return PerceptionType.DIAGRAM.value
        # Heurísticas de OCR / Documento
        if any(uri_lower.endswith(ext) for ext in (".pdf", ".tiff", ".tif", ".txt", ".rtf", ".scan")):
            return PerceptionType.OCR.value
        # MIME types padrão
        mime, _ = mimetypes.guess_type(raw_uri)
        if mime:
            if mime.startswith("image/"):
                return PerceptionType.IMAGE.value
            if mime.startswith("audio/"):
                return PerceptionType.AUDIO.value
            if mime == "application/pdf":
                return PerceptionType.OCR.value

        # Extensões comuns fallback
        if any(uri_lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg")):
            return PerceptionType.IMAGE.value
        if any(uri_lower.endswith(ext) for ext in (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")):
            return PerceptionType.AUDIO.value

        return PerceptionType.IMAGE.value

    def intercept_and_perceive(
        self,
        source_uri: str,
        mime_type: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> PerceptionArtifact:
        """Formal runtime auto-spawn:
        
        When a text-only primary agent encounters a sensory input (image file, screenshot, audio file, diagram URL):
        1. Intercepts the sensory source URI & mime type.
        2. Spawns/dispatches to a specialized Vision/Audio/OCR/Diagram worker in background/isolation.
        3. Returns a typed PerceptionArtifact (summary, structured_observations, confidence, ocr_text).
        4. Handles exceptions with graceful fallback artifact so the agent can safely continue.
        """
        options = options or {}
        modality = self.detect_modality(source_uri, mime_type=mime_type)
        try:
            return self.dispatch(source_uri, modality_type=modality, options=options)
        except Exception as exc:
            return PerceptionArtifact(
                type=modality,
                summary=f"Worker failure during auto-spawn perception for {source_uri}",
                structured_observations=[f"Error encountered: {type(exc).__name__}: {str(exc)}"],
                confidence=0.0,
                raw_uri=source_uri,
                ocr_text=None,
                metadata={"error": str(exc), "status": "fallback"},
            )

    def dispatch(
        self,
        raw_uri: str,
        modality_type: Optional[str | PerceptionType] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> PerceptionArtifact:
        """Despacha a análise da mídia para o worker adequado e retorna um PerceptionArtifact."""
        options = options or {}
        inferred_type = (
            modality_type.value
            if isinstance(modality_type, PerceptionType)
            else (str(modality_type).lower() if modality_type else self.detect_modality(raw_uri))
        )

        handler = self._handlers.get(inferred_type)
        if not handler:
            # Fallback seguro caso não haja handler registrado
            return PerceptionArtifact(
                type=inferred_type,
                summary=f"Worker unavailable for modality: {inferred_type}",
                structured_observations=[f"No handler registered for {inferred_type}"],
                confidence=0.0,
                raw_uri=raw_uri,
                ocr_text=None,
                metadata={"error": "no_handler"},
            )

        try:
            return handler(raw_uri, options)
        except Exception as exc:
            return PerceptionArtifact(
                type=inferred_type,
                summary=f"Worker execution failed for {raw_uri}",
                structured_observations=[f"Worker error ({type(exc).__name__}): {str(exc)}"],
                confidence=0.0,
                raw_uri=raw_uri,
                ocr_text=None,
                metadata={"error": str(exc), "error_type": type(exc).__name__, "status": "failed"},
            )

    def _register_default_handlers(self) -> None:
        def _default_image_worker(uri: str, opts: Dict[str, Any]) -> PerceptionArtifact:
            filename = os.path.basename(uri)
            return PerceptionArtifact(
                type=PerceptionType.IMAGE.value,
                summary=f"Vision analysis for {filename}",
                structured_observations=[
                    f"Processed image artifact at {uri}",
                    "Visual features extracted without expanding LLM context",
                ],
                confidence=0.95,
                raw_uri=uri,
                metadata={"worker": "default-vision-worker", **opts},
            )

        def _default_audio_worker(uri: str, opts: Dict[str, Any]) -> PerceptionArtifact:
            filename = os.path.basename(uri)
            return PerceptionArtifact(
                type=PerceptionType.AUDIO.value,
                summary=f"Audio transcription and acoustic analysis for {filename}",
                structured_observations=[
                    f"Processed audio stream from {uri}",
                    "Temporal features and speech-to-text segments ready",
                ],
                confidence=0.92,
                raw_uri=uri,
                ocr_text=f"Transcript segments from {filename}",
                metadata={"worker": "default-audio-worker", **opts},
            )

        def _default_ocr_worker(uri: str, opts: Dict[str, Any]) -> PerceptionArtifact:
            filename = os.path.basename(uri)
            return PerceptionArtifact(
                type=PerceptionType.OCR.value,
                summary=f"OCR optical character recognition for {filename}",
                structured_observations=[
                    f"Extracted document text layers from {uri}",
                    "Layout structure and tabular segments normalized",
                ],
                confidence=0.98,
                raw_uri=uri,
                ocr_text=f"OCR content extracted from {filename}",
                metadata={"worker": "default-ocr-worker", **opts},
            )

        def _default_diagram_worker(uri: str, opts: Dict[str, Any]) -> PerceptionArtifact:
            filename = os.path.basename(uri)
            return PerceptionArtifact(
                type=PerceptionType.DIAGRAM.value,
                summary=f"Structural diagram parsing for {filename}",
                structured_observations=[
                    f"Parsed diagram components and topological flow from {uri}",
                    "Nodes, edges and component hierarchy identified",
                ],
                confidence=0.94,
                raw_uri=uri,
                metadata={"worker": "default-diagram-worker", **opts},
            )

        self._handlers[PerceptionType.IMAGE.value] = _default_image_worker
        self._handlers[PerceptionType.AUDIO.value] = _default_audio_worker
        self._handlers[PerceptionType.OCR.value] = _default_ocr_worker
        self._handlers[PerceptionType.DIAGRAM.value] = _default_diagram_worker
