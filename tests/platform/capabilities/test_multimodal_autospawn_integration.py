"""Tests for Multimodal Auto-Spawn Pattern integration.

Invariants validated:
1. Formal auto-spawn interception:
   - When a text-only agent encounters sensory inputs (images, audio, diagram, ocr documents):
     MultimodalDispatchPattern.intercept_and_perceive(source_uri, mime_type) triggers.
2. Background / isolated specialized worker invocation:
   - Specialized workers (Vision, Audio, Diagram, OCR) produce a typed PerceptionArtifact.
   - PerceptionArtifact contains summary, structured_observations, confidence, ocr_text, raw_uri.
3. PerceptionArtifact context rendering and injection:
   - render_for_prompt() returns structured tag-delimited context for text model prompt without context explosion.
   - format_for_context() returns concise formatted perception.
4. Graceful fallback on worker failure:
   - If a multimodal worker raises an exception or fails, intercept_and_perceive catches it and returns
     a fallback PerceptionArtifact with confidence=0.0 and error details, allowing the text agent to proceed safely.
"""

import unittest
from typing import Dict, Any

from hermes.platform.capabilities.resolver import (
    MultimodalDispatchPattern,
    PerceptionArtifact,
    PerceptionType,
    CapabilityRegistry,
)


class TestMultimodalAutoSpawnIntegration(unittest.TestCase):
    def setUp(self):
        self.pattern = MultimodalDispatchPattern()

    def test_resolver_exports_multimodal_pattern(self):
        """Resolver exports MultimodalDispatchPattern and PerceptionArtifact."""
        self.assertIsNotNone(MultimodalDispatchPattern)
        self.assertIsNotNone(PerceptionArtifact)
        self.assertIsNotNone(PerceptionType)
        reg = CapabilityRegistry()
        cap = reg.get("multimodal-perception")
        self.assertIsNotNone(cap)
        self.assertIn("auto-spawn", cap.features)

    def test_autospawn_image_interception(self):
        """Interception for image input produces image PerceptionArtifact."""
        artifact = self.pattern.intercept_and_perceive(
            source_uri="file:///tmp/screenshots/active_window.png",
            mime_type="image/png",
        )
        self.assertIsInstance(artifact, PerceptionArtifact)
        self.assertEqual(artifact.type, PerceptionType.IMAGE.value)
        self.assertIn("Vision analysis", artifact.summary)
        self.assertTrue(len(artifact.structured_observations) > 0)
        self.assertGreater(artifact.confidence, 0.8)
        self.assertEqual(artifact.raw_uri, "file:///tmp/screenshots/active_window.png")

    def test_autospawn_audio_interception(self):
        """Interception for audio input produces audio PerceptionArtifact."""
        artifact = self.pattern.intercept_and_perceive(
            source_uri="https://storage.local/recordings/meeting_voice.mp3",
            mime_type="audio/mp3",
        )
        self.assertIsInstance(artifact, PerceptionArtifact)
        self.assertEqual(artifact.type, PerceptionType.AUDIO.value)
        self.assertIn("Audio", artifact.summary)
        self.assertTrue(any("speech-to-text" in obs for obs in artifact.structured_observations))
        self.assertGreater(artifact.confidence, 0.8)
        self.assertEqual(artifact.raw_uri, "https://storage.local/recordings/meeting_voice.mp3")

    def test_autospawn_diagram_interception(self):
        """Interception for diagram input produces diagram PerceptionArtifact."""
        artifact = self.pattern.intercept_and_perceive(
            source_uri="architecture.mmd",
        )
        self.assertEqual(artifact.type, PerceptionType.DIAGRAM.value)
        self.assertIn("diagram", artifact.summary.lower())

    def test_perception_artifact_render_for_prompt(self):
        """PerceptionArtifact render_for_prompt() formats clean context block."""
        artifact = PerceptionArtifact(
            type="image",
            summary="Login screen showing email input and sign-in button",
            structured_observations=[
                "Email text field is visible at coordinates (100, 200)",
                "Sign-in button is enabled with blue background",
            ],
            confidence=0.97,
            raw_uri="screenshots/login.png",
            ocr_text="Welcome back! Sign In",
        )
        rendered = artifact.render_for_prompt()
        self.assertIn("<multimodal_perception", rendered)
        self.assertIn('type="image"', rendered)
        self.assertIn('confidence="0.97"', rendered)
        self.assertIn("Source URI: screenshots/login.png", rendered)
        self.assertIn("Summary: Login screen showing email input and sign-in button", rendered)
        self.assertIn("- Email text field is visible at coordinates (100, 200)", rendered)
        self.assertIn("- Sign-in button is enabled with blue background", rendered)
        self.assertIn("Extracted Text/OCR:\nWelcome back! Sign In", rendered)
        self.assertIn("</multimodal_perception>", rendered)

    def test_perception_artifact_render_for_prompt_no_ocr(self):
        """PerceptionArtifact render_for_prompt() handles missing ocr_text gracefully."""
        artifact = PerceptionArtifact(
            type="audio",
            summary="Brief beep sound",
            structured_observations=["High frequency tone"],
            confidence=0.85,
            raw_uri="beep.wav",
        )
        rendered = artifact.render_for_prompt()
        self.assertIn("<multimodal_perception", rendered)
        self.assertNotIn("Extracted Text/OCR", rendered)

    def test_perception_artifact_to_dict(self):
        """to_dict includes ocr_text and canonical fields."""
        artifact = PerceptionArtifact(
            type="ocr",
            summary="PDF invoice scanned",
            structured_observations=["Invoice total $450.00"],
            confidence=0.99,
            raw_uri="invoice.pdf",
            ocr_text="Invoice #12345 Total: $450.00",
            metadata={"source": "upload"},
        )
        d = artifact.to_dict()
        self.assertEqual(d["type"], "ocr")
        self.assertEqual(d["ocr_text"], "Invoice #12345 Total: $450.00")
        self.assertEqual(d["confidence"], 0.99)
        self.assertEqual(d["metadata"]["source"], "upload")

    def test_graceful_fallback_if_worker_raises(self):
        """If worker fails or raises an unhandled error, auto-spawn returns fallback artifact."""
        def failing_worker(uri: str, opts: Dict[str, Any]) -> PerceptionArtifact:
            raise RuntimeError("Out of VRAM: Model died during vision inference")

        self.pattern.register_worker(PerceptionType.IMAGE, failing_worker)

        artifact = self.pattern.intercept_and_perceive("broken_image.png", mime_type="image/png")
        self.assertIsInstance(artifact, PerceptionArtifact)
        self.assertEqual(artifact.confidence, 0.0)
        self.assertIn("Worker execution failed", artifact.summary)
        self.assertTrue(any("RuntimeError" in obs for obs in artifact.structured_observations))
        self.assertEqual(artifact.metadata.get("status"), "failed")

        # Context rendering of fallback still produces valid prompt injection without blowing up
        rendered = artifact.render_for_prompt()
        self.assertIn("<multimodal_perception", rendered)
        self.assertIn('confidence="0.00"', rendered)


if __name__ == "__main__":
    unittest.main()
