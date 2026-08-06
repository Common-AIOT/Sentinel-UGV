import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from evaluation.noise_corpus import (
    build_corpus,
    mix_at_snr,
    mix_components_at_snr,
    rms,
    synthesize_noise,
)
from evaluation.pipeline_audio_corpus import build_pipeline_corpus


class NoiseCorpusTest(unittest.TestCase):
    def test_profiles_are_deterministic_and_non_silent(self):
        for profile in ("motor-fan", "alarm", "rubble"):
            first = synthesize_noise(profile, 16000, 16000, seed=7)
            second = synthesize_noise(profile, 16000, 16000, seed=7)
            np.testing.assert_array_equal(first, second)
            self.assertAlmostEqual(1.0, rms(first), places=5)

    def test_mix_reaches_requested_snr_without_clipping(self):
        t = np.arange(16000, dtype=np.float32) / 16000
        speech = 0.1 * np.sin(2 * np.pi * 220 * t)
        noise = synthesize_noise("motor-fan", len(speech), 16000, seed=3)
        mixed = mix_at_snr(speech, noise, 5)
        # 클리핑 방지용 전체 gain은 신호·잡음 비율을 바꾸지 않는다.
        self.assertLessEqual(float(np.max(np.abs(mixed))), 0.95)
        expected_noise = rms(speech) / (10 ** (5 / 20))
        self.assertAlmostEqual(expected_noise, rms(noise * expected_noise), places=5)

        speech_part, noise_part, reconstructed = mix_components_at_snr(
            speech, noise, 5
        )
        np.testing.assert_allclose(reconstructed, speech_part + noise_part, atol=1e-7)

    def test_build_uses_original_as_read_only_and_writes_condition_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            wav = source / "speech.wav"
            samples = np.sin(2 * np.pi * 220 * np.arange(8000) / 16000).astype(np.float32) * 0.1
            sf.write(wav, samples, 16000, subtype="PCM_16")
            original = wav.read_bytes()
            row = {
                "caseId": "ko-one",
                "audio": "speech.wav",
                "transcript": "못 움직여요",
                "language": "ko",
                "condition": "synthetic-clean",
                "criticalTermGroups": [["못"], ["움직"]],
                "expectedPolarity": "risk",
                "riskPatterns": ["못 움직"],
                "safePatterns": ["움직일 수 있"],
            }
            manifest = source / "manifest.jsonl"
            manifest.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

            generated = build_corpus(
                manifest,
                output,
                profiles=("motor-fan",),
                snrs=(5.0,),
            )

            rows = [json.loads(line) for line in generated.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(2, len(rows))
            self.assertEqual({"clean", "motor-fan@5db"}, {row["condition"] for row in rows})
            self.assertTrue(
                (output / "noise-components" / "motor-fan" / "5db" / "speech.wav").is_file()
            )
            self.assertTrue(
                (output / "speech-components" / "motor-fan" / "5db" / "speech.wav").is_file()
            )
            self.assertEqual(original, wav.read_bytes())

    def test_record_corpus_schema_and_external_noise_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            samples = np.sin(2 * np.pi * 220 * np.arange(8000) / 16000).astype(np.float32) * 0.1
            sf.write(source / "voice.wav", samples, 16000)
            sf.write(source / "motor.wav", np.random.default_rng(1).normal(0, 0.1, 4000), 8000)
            row = {
                "file": "voice.wav",
                "lineNumber": 1,
                "question": "MOBILITY",
                "text": "다리가 눌려서 못 움직여요",
                "expectedField": "mobilityStatus",
                "expectedValue": "NO",
                "condition": "quiet",
            }
            manifest = source / "manifest.jsonl"
            manifest.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

            generated = build_corpus(
                manifest,
                root / "output",
                noise_files=(source / "motor.wav",),
                snrs=(0.0,),
            )
            rows = [json.loads(line) for line in generated.read_text(encoding="utf-8").splitlines()]
            speech = next(row for row in rows if row["transcript"])
            self.assertEqual("risk", speech["expectedPolarity"])
            self.assertIn(["못"], speech["criticalTermGroups"])
            self.assertIn("motor@0db", {row["condition"] for row in rows})
            self.assertIn("noise-only-motor", {row["condition"] for row in rows})

    def test_pipeline_corpus_is_16k_mono_and_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            stereo = np.column_stack(
                [np.full(48000, 0.01, dtype=np.float32), np.full(48000, 0.02, dtype=np.float32)]
            )
            sf.write(source / "voice.wav", stereo, 48000)
            row = {
                "caseId": "one",
                "audio": "voice.wav",
                "transcript": "네",
                "language": "ko",
                "condition": "clean",
                "criticalTermGroups": [["네"]],
                "expectedPolarity": "unknown",
                "riskPatterns": [],
                "safePatterns": [],
            }
            manifest = source / "manifest.jsonl"
            manifest.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

            generated = build_pipeline_corpus(manifest, root / "output")
            converted, sample_rate = sf.read(root / "output" / "voice.wav", dtype="float32")
            self.assertEqual(16000, sample_rate)
            self.assertEqual(1, converted.ndim)
            self.assertAlmostEqual(0.08, rms(converted), places=3)
            self.assertTrue(generated.is_file())


if __name__ == "__main__":
    unittest.main()
