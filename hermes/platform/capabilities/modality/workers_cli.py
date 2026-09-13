"""Modality workers REAIS de documento/áudio/vídeo (A5).

Padrão estilo LSP K6: transporte real quando o binário externo existe
(``pdftotext`` p/ PDF, ``ffprobe`` p/ áudio/vídeo), fail-closed sem ele.
NUNCA fabricam leitura de conteúdo: sem CLI (ou sem arquivo) -> 
``ModalityUnavailableError``; o que é metadado determinístico entra em
``structured_data``/``observations``; interpretação de conteúdo (ASR,
frame-understanding) fica EXPLICITAMENTE delegada à lane de visão/modelo —
o worker registra no artifact que aquilo exige a lane agêntica, não inventa
resposta. A extração é feita por subprocesso peer (nunca import de runtime
externo no processo do agente).
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hermes.platform.capabilities.modality.workers import PerceptionArtifact

_EXT_TEXT = {".txt", ".md", ".rst", ".csv", ".log", ".json", ".yaml", ".yml"}


class ModalityUnavailableError(RuntimeError):
    """CLI de extração ausente ou arquivo ilegível (fail-closed)."""


@dataclass
class _CliSpec:
    binary: str
    required: bool = True


class _CliWorker:
    """Base: resolução do binário (override > PATH), probe e execução."""

    modality: str = ""
    provider_id: str = ""
    cli_name: str = ""

    def __init__(self, produced_by: Optional[str] = None,
                 cli_path: Optional[str] = None,
                 model_identity: Optional[Dict[str, str]] = None):
        self.produced_by = produced_by or self.provider_id
        self.cli_path = cli_path
        self.model_identity = model_identity or {
            "family": "cli-extraction", "variant": "deterministic"
        }

    def _binary(self) -> Optional[str]:
        if self.cli_path is not None:
            return self.cli_path if os.access(self.cli_path, os.X_OK) else None
        return shutil.which(self.cli_name)

    def available(self) -> bool:
        """Probe sem efeito colateral: binário existe (e é executável)."""
        return self._binary() is not None

    def _run(self, args: List[str]) -> str:
        binary = self._binary()
        if binary is None:
            raise ModalityUnavailableError(
                f"{self.provider_id}: CLI '{self.cli_name}' não está "
                f"disponível (fail-closed)"
            )
        proc = subprocess.run(
            [binary] + args,
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise ModalityUnavailableError(
                f"{self.provider_id}: {self.cli_name} rc={proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:300]}"
            )
        return proc.stdout

    def _new_artifact(self, source_uri: str, summary: str, **kw: Any) -> PerceptionArtifact:
        base = dict(
            modality=self.modality,
            source_uri=source_uri,
            summary=summary,
            produced_by=self.produced_by,
            model_identity=self.model_identity,
        )
        base.update(kw)
        return PerceptionArtifact(artifact_id=f"art-{self.provider_id}-000", **base)

    def _require_source(self, source_uri: str) -> None:
        if not source_uri or not os.path.isfile(source_uri):
            raise ModalityUnavailableError(
                f"{self.provider_id}: source not readable: {source_uri!r}"
            )


class DocumentWorker(_CliWorker):
    """Documento/PDF: extração de texto determinística via pdftotext (poppler)
    ou leitura direta p/ formatos texto puros (sem CLI externa).

    ``available()`` = probe do pdftotext (o pipeline de PDF); texto puro não
    exige CLI nenhuma e funciona mesmo com available()==False."""

    modality = "document"
    provider_id = "document-worker"
    cli_name = "pdftotext"

    def analyze_document(self, source_uri: str) -> PerceptionArtifact:
        self._require_source(source_uri)
        ext = os.path.splitext(source_uri)[1].lower()
        if ext == ".pdf":
            text = self._run([source_uri, "-"])
            modality = "pdf"
        elif ext in _EXT_TEXT:
            with open(source_uri, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            modality = "document"
        else:
            raise ModalityUnavailableError(
                f"{self.provider_id}: extensão {ext!r} sem extrator "
                f"(pdf ou texto puro)"
            )
        lines = text.splitlines()
        words = len(text.split())
        return self._new_artifact(
            source_uri,
            summary=f"Extracted {len(lines)} lines / {words} words "
                    f"(deterministic)",
            modality=modality,
            extracted_text=text,
            uncertainty="low",
            structured_data={"lines": len(lines), "words": words,
                             "chars": len(text)},
            observations=[f"Modality '{modality}' extracted via "
                          f"{self.cli_name if ext == '.pdf' else 'direct read'}"],
        )


class _FfprobeWorker(_CliWorker):
    """Base dos workers de mídia: metadados via ffprobe (key=value, sem libs)."""

    cli_name = "ffprobe"
    modality = "media"

    def _probe_media(self, source_uri: str) -> Dict[str, Any]:
        self._require_source(source_uri)
        out = self._run(["-v", "error", "-show_streams",
                         "-show_format", source_uri])
        meta: Dict[str, str] = {}
        for line in out.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                meta[key.strip()] = value.strip()
        return meta


class AudioWorker(_FfprobeWorker):
    modality = "audio"
    provider_id = "audio-worker"

    def analyze_audio(self, source_uri: str) -> PerceptionArtifact:
        meta = self._probe_media(source_uri)
        duration = meta.get("duration", "")
        return self._new_artifact(
            source_uri,
            summary=f"Audio metadata captured (duration={duration}s)",
            extracted_text="",
            uncertainty="low",
            structured_data={
                "duration_s": duration,
                "codec": meta.get("codec_name", ""),
                "sample_rate": meta.get("sample_rate", ""),
                "channels": meta.get("channels", ""),
                "format": meta.get("format_name", ""),
            },
            observations=[
                "Transcript (ASR) is NOT produced by this worker: it requires "
                "the model-based audio lane (out-of-process).",
            ],
        )


class VideoWorker(_FfprobeWorker):
    modality = "video"
    provider_id = "video-worker"

    def analyze_video(self, source_uri: str) -> PerceptionArtifact:
        meta = self._probe_media(source_uri)
        duration = meta.get("duration", "")
        return self._new_artifact(
            source_uri,
            summary=f"Video metadata captured (duration={duration}s)",
            uncertainty="low",
            structured_data={
                "duration_s": duration,
                "video_codec": meta.get("codec_name", ""),
                "width": meta.get("width", ""),
                "height": meta.get("height", ""),
                "fps": meta.get("avg_frame_rate", meta.get("r_frame_rate", "")),
            },
            observations=[
                "Frame interpretation requires the vision lane (agentic, "
                "out-of-process) — this worker never fabricates content.",
            ],
        )


def register_modality_workers(
    registry,
    *,
    document: Optional[DocumentWorker] = None,
    audio: Optional[AudioWorker] = None,
    video: Optional[VideoWorker] = None,
) -> List[_CliWorker]:
    """Liga os workers ao CapabilityRegistry (capacidades + providers vivos).

    Opt-in explícito: NÃO altera defaults do registry (assembly preservado)."""
    workers = [document or DocumentWorker(),
               audio or AudioWorker(),
               video or VideoWorker()]
    caps = {
        "document-understanding": "document-worker",
        "audio-understanding": "audio-worker",
        "video-understanding": "video-worker",
    }
    for worker in workers:
        registry.register_provider(worker)
    for cap_id, provider_id in caps.items():
        registry.add_provider(cap_id, provider_id)
    return workers
