"""A5 — Modality workers reais (documento/áudio/vídeo) via CLI peer (invariantes).

Contratos:
(a) probe fail-closed: sem binário (override inexistente) -> available() False
    e analyze() levanta ModalityUnavailableError (nunca fabrica conteúdo).
(b) DocumentWorker: texto puro é lido direto (sem CLI); PDF usa pdftotext
    (peer scriptado no teste) e o artifact carrega modality "pdf" + texto.
(c) Audio/VideoWorker: metadados determinísticos via ffprobe peer; interpretação
    de conteúdo é EXPLICITAMENTE delegada (observations marcam a lane de
    modelo) — worker nunca inventa ASR/quadros.
(d) register_modality_workers liga capacidades + providers vivos ao registry
    sem tocar nos defaults.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from hermes.platform.capabilities.registry import CapabilityRegistry
from hermes.platform.capabilities.modality.workers_cli import (
    DocumentWorker, AudioWorker, VideoWorker, ModalityUnavailableError,
    register_modality_workers,
)


def _write_fake(bin_dir: Path, name: str, output: str) -> str:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\ncat <<'EOF'\n{output}\nEOF\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


class TestCliProbe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_unavailable_cli_fails_closed(self):
        pdf = DocumentWorker(cli_path="/nonexistent/pdftotext")
        self.assertFalse(pdf.available())
        src = self.root / "a.pdf"
        src.write_bytes(b"%PDF-1.4 fake")
        with self.assertRaises(ModalityUnavailableError):
            pdf.analyze_document(str(src))

    def test_audio_video_unavailable_without_ffprobe(self):
        audio = AudioWorker(cli_path="/nonexistent/ffprobe")
        video = VideoWorker(cli_path="/nonexistent/ffprobe")
        self.assertFalse(audio.available())
        self.assertFalse(video.available())
        src = self.root / "a.mp3"
        src.write_bytes(b"\xff\xfb fake")
        with self.assertRaises(ModalityUnavailableError):
            audio.analyze_audio(str(src))
        with self.assertRaises(ModalityUnavailableError):
            video.analyze_video(str(src))


class TestDocumentWorker(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "bin").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_text_read_directly_without_cli(self):
        src = self.root / "note.txt"
        src.write_text("DECIDED: usar OIDC\nmais contexto aqui\n",
                       encoding="utf-8")
        worker = DocumentWorker()
        art = worker.analyze_document(str(src))
        self.assertEqual(art.modality, "document")
        self.assertIn("DECIDED: usar OIDC", art.extracted_text)
        self.assertEqual(art.produced_by, "document-worker")
        self.assertEqual(art.structured_data["words"], 6)

    def test_pdf_via_peer_pdftotext(self):
        pdf_cli = _write_fake(
            self.root / "bin", "pdftotext",
            "DECIDED: contrato assinado\nsegunda página\n")
        src = self.root / "doc.pdf"
        src.write_bytes(b"%PDF-1.4 fake")
        worker = DocumentWorker(cli_path=pdf_cli)
        self.assertTrue(worker.available())
        art = worker.analyze_document(str(src))
        self.assertEqual(art.modality, "pdf")
        self.assertIn("DECIDED: contrato assinado", art.extracted_text)

    def test_unknown_extension_fails_closed(self):
        src = self.root / "x.xyz"
        src.write_bytes(b"data")
        with self.assertRaises(ModalityUnavailableError):
            DocumentWorker().analyze_document(str(src))


class TestMediaWorkers(unittest.TestCase):
    FFMPEG_OUT = (
        "codec_name=h264\n"
        "width=1920\nheight=1080\n"
        "avg_frame_rate=30/1\n"
        "duration=12.5\n"
        "format_name=mp4\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.ffprobe = _write_fake(self.bin, "ffprobe", self.FFMPEG_OUT)
        self.src = self.root / "clip.mp4"
        self.src.write_bytes(b"fake media")

    def tearDown(self):
        self._tmp.cleanup()

    def test_video_metadata_deterministic_and_no_fabrication(self):
        worker = VideoWorker(cli_path=self.ffprobe)
        self.assertTrue(worker.available())
        art = worker.analyze_video(str(self.src))
        self.assertEqual(art.modality, "video")
        self.assertEqual(art.structured_data["width"], "1920")
        self.assertEqual(art.structured_data["fps"], "30/1")
        self.assertIn("duration_s", art.structured_data)
        self.assertEqual(art.uncertainty, "low")
        # Nunca fabrica conteúdo: a interpretação é delegada, não inventada.
        self.assertTrue(any("vision lane" in o for o in art.observations))
        self.assertEqual(art.extracted_text, "")

    def test_audio_worker_reports_asr_delegation(self):
        worker = AudioWorker(cli_path=self.ffprobe)
        art = worker.analyze_audio(str(self.src))
        self.assertEqual(art.modality, "audio")
        self.assertEqual(art.structured_data["codec"], "h264")
        self.assertTrue(any("ASR" in o for o in art.observations))
        self.assertTrue(any("out-of-process" in o for o in art.observations))


class TestRegistryWiring(unittest.TestCase):
    def test_register_modality_workers_is_opt_in(self):
        reg = CapabilityRegistry()
        # Antes: nada de modality (defaults preservados).
        self.assertIsNone(reg.get("document-understanding"))
        register_modality_workers(reg)
        doc = reg.get("document-understanding")
        self.assertIsNotNone(doc)
        self.assertIn("document-worker", doc.providers)
        self.assertIsNotNone(reg.get_provider("audio-worker"))
        self.assertIsNotNone(reg.get_provider("video-worker"))
        # CapabilityResolver enxerga via get.
        self.assertEqual(reg.get("video-understanding").execution_kind, "agentic")


if __name__ == "__main__":
    unittest.main()
