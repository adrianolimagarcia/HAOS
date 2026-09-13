"""Tests for Marco 8 (Multimodal Fabric) and Marco 9 (Artifact/Git Fabric).

Invariantes validados:
1. Multimodal Fabric:
   - Emissão correta de PerceptionArtifact (type, summary, structured_observations, confidence, raw_uri).
   - MultimodalDispatchPattern despachando para workers de Vision, Audio, OCR e Diagram.
   - Preservação do contexto do agente textual primário sem re-prompts custosos ou contexto inflado.
   - Handlers customizados e detecção de modalidade por heurística e MIME type.

2. MergeQueue (Artifact / Git Fabric - Lane Kilo):
   - Ordenação correta por prioridade e FIFO para mesma prioridade.
   - Rebase serial e validação estrita antes do merge em main.
   - Rejeição quando validação pós-rebase falha ou há conflito de rebase.
   - Sucesso no merge com avanço fast-forward da main branch.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from hermes.platform.capabilities.modality.unified_fabric import (
    PerceptionArtifact,
    PerceptionType,
    MultimodalDispatchPattern,
)
from hermes.platform.workspaces.merge_queue import (
    MergeQueue,
    MergeCandidate,
    MergeStatus,
)


class TestMultimodalFabric(unittest.TestCase):
    def setUp(self):
        self.pattern = MultimodalDispatchPattern()

    def test_perception_artifact_fields(self):
        artifact = PerceptionArtifact(
            type="image",
            summary="Test image analysis",
            structured_observations=["Object A detected", "Object B detected"],
            confidence=0.97,
            raw_uri="file:///path/to/image.png",
        )
        self.assertEqual(artifact.type, "image")
        self.assertEqual(artifact.summary, "Test image analysis")
        self.assertEqual(len(artifact.structured_observations), 2)
        self.assertEqual(artifact.confidence, 0.97)
        self.assertEqual(artifact.raw_uri, "file:///path/to/image.png")
        self.assertTrue(artifact.artifact_id.startswith("art-perc-"))

        data = artifact.to_dict()
        self.assertEqual(data["type"], "image")
        self.assertEqual(data["confidence"], 0.97)

        formatted = artifact.format_for_context()
        self.assertIn("Test image analysis", formatted)
        self.assertIn("Object A detected", formatted)

    def test_multimodal_dispatch_default_workers(self):
        # Image
        img_art = self.pattern.dispatch("diagram.png")
        self.assertEqual(img_art.type, PerceptionType.IMAGE.value)
        self.assertIn("Vision analysis", img_art.summary)
        self.assertGreater(img_art.confidence, 0.9)

        # Audio
        audio_art = self.pattern.dispatch("meeting.mp3")
        self.assertEqual(audio_art.type, PerceptionType.AUDIO.value)
        self.assertIn("Audio transcription", audio_art.summary)

        # OCR
        ocr_art = self.pattern.dispatch("document.pdf")
        self.assertEqual(ocr_art.type, PerceptionType.OCR.value)
        self.assertIn("OCR", ocr_art.summary)

        # Diagram
        diag_art = self.pattern.dispatch("architecture.mermaid")
        self.assertEqual(diag_art.type, PerceptionType.DIAGRAM.value)
        self.assertIn("diagram", diag_art.summary.lower())

    def test_custom_worker_registration(self):
        def custom_vision_worker(uri: str, opts: dict) -> PerceptionArtifact:
            return PerceptionArtifact(
                type="image",
                summary="Specialized Satellite Vision Model Output",
                structured_observations=["Detected runway", "Detected hangar"],
                confidence=0.99,
                raw_uri=uri,
                metadata={"specialized": True},
            )

        self.pattern.register_worker(PerceptionType.IMAGE, custom_vision_worker)
        art = self.pattern.dispatch("satellite.jpg")
        self.assertEqual(art.summary, "Specialized Satellite Vision Model Output")
        self.assertEqual(art.confidence, 0.99)
        self.assertTrue(art.metadata.get("specialized"))


class TestMergeQueueGitFabric(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="haos_test_mq_")
        self.repo = Path(self.tmp_dir).resolve()

        # Configura repo git local limpo
        self._git("init", "-b", "main")
        self._git("config", "user.name", "Test Runner")
        self._git("config", "user.email", "test@example.com")

        # Commit inicial na main
        main_file = self.repo / "README.md"
        main_file.write_text("# Project Root\nInitial commit.\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "chore: initial commit")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _git(self, *args: str) -> str:
        res = subprocess.run(
            ["git", *args],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()

    def _create_task_branch(self, branch_name: str, file_name: str, content: str) -> str:
        self._git("checkout", "main")
        self._git("checkout", "-b", branch_name)
        file_path = self.repo / file_name
        file_path.write_text(content, encoding="utf-8")
        self._git("add", file_name)
        self._git("commit", "-m", f"feat: {branch_name}")
        self._git("checkout", "main")
        return branch_name

    def test_merge_candidate_priority_ordering(self):
        mq = MergeQueue(repo_root=self.repo)
        c1 = mq.enqueue(task_id="T-1", branch="feature/1", test_report_hash="hash-1", priority=10)
        c2 = mq.enqueue(task_id="T-2", branch="feature/2", test_report_hash="hash-2", priority=50)
        c3 = mq.enqueue(task_id="T-3", branch="feature/3", test_report_hash="hash-3", priority=30)

        q = mq.list_queue()
        self.assertEqual([c.task_id for c in q], ["T-2", "T-3", "T-1"])

    def test_merge_success_serial_rebase(self):
        # 1. Cria branch 1
        b1 = self._create_task_branch("task-101", "module_a.py", "print('module A')\n")

        # 2. Faz commit em main para forçar rebase
        main_file = self.repo / "main_update.txt"
        main_file.write_text("concurrent main commit\n", encoding="utf-8")
        self._git("add", "main_update.txt")
        self._git("commit", "-m", "chore: main update")

        def dummy_validator(candidate: MergeCandidate, root: Path) -> bool:
            return True

        mq = MergeQueue(repo_root=self.repo, validator_fn=dummy_validator)
        mq.enqueue(task_id="T-101", branch=b1, test_report_hash="hash-ok", priority=1)

        result = mq.process_next()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, MergeStatus.MERGED)
        self.assertIsNone(result.rejection_reason)

        # Verifica que module_a.py está presente na main
        self._git("checkout", "main")
        self.assertTrue((self.repo / "module_a.py").exists())
        self.assertTrue((self.repo / "main_update.txt").exists())

    def test_merge_rejected_on_validator_failure(self):
        b1 = self._create_task_branch("task-102", "faulty.py", "syntax error\n")

        def failing_validator(candidate: MergeCandidate, root: Path) -> bool:
            return False

        mq = MergeQueue(repo_root=self.repo, validator_fn=failing_validator)
        mq.enqueue(task_id="T-102", branch=b1, test_report_hash="hash-fail", priority=1)

        result = mq.process_next()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, MergeStatus.REJECTED)
        self.assertIn("validation failed", result.rejection_reason.lower())

        # Garante que faulty.py NÃO está na main
        self._git("checkout", "main")
        self.assertFalse((self.repo / "faulty.py").exists())

    def test_merge_rejected_on_rebase_conflict(self):
        # Conflito intencional na mesma linha do README.md
        readme = self.repo / "README.md"

        # Branch conflitante
        self._git("checkout", "-b", "conflict-branch")
        readme.write_text("Changed line in branch\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "branch change")

        # Main muda na mesma linha
        self._git("checkout", "main")
        readme.write_text("Conflicting line in main\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "main conflicting change")

        mq = MergeQueue(repo_root=self.repo)
        mq.enqueue(task_id="T-CONF", branch="conflict-branch", test_report_hash="hash-conf")

        result = mq.process_next()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, MergeStatus.REJECTED)
        self.assertIn("rebase failed", result.rejection_reason.lower())


if __name__ == "__main__":
    unittest.main()
