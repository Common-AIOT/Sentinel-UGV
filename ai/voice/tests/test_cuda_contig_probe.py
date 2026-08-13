import unittest

from evaluation.cuda_contig_probe import ATTEMPT_SIZES_MB, run_probe


def fail_above(limit_mb):
    def allocate(size_mb):
        if size_mb > limit_mb:
            raise RuntimeError(f"fake OOM at {size_mb}MB")

    return allocate


class CudaContigProbeTest(unittest.TestCase):
    def test_descending_probe_stops_at_first_success(self):
        result = run_probe(
            fail_above(1000),
            ATTEMPT_SIZES_MB,
            available_mb=3000,
            safety_margin_mb=768,
        )
        self.assertEqual(result["max_ok_mb"], 768)
        self.assertEqual(result["min_failed_mb"], 1024)
        self.assertEqual(result["skipped_safety_mb"], [2400])
        # 첫 성공 이후에는 더 작은 크기를 시도하지 않는다
        tried = [a["size_mb"] for a in result["attempts"]]
        self.assertNotIn(512, tried)

    def test_low_available_skips_large_sizes_for_safety(self):
        result = run_probe(
            fail_above(1000),
            ATTEMPT_SIZES_MB,
            available_mb=900,
            safety_margin_mb=768,
        )
        self.assertEqual(result["max_ok_mb"], 128)
        self.assertEqual(result["min_failed_mb"], None)
        self.assertEqual(
            result["skipped_safety_mb"],
            [2400, 2048, 1792, 1536, 1280, 1024, 768, 512, 384, 256],
        )

    def test_all_sizes_fail_reports_zero_max(self):
        result = run_probe(
            fail_above(0),
            ATTEMPT_SIZES_MB,
            available_mb=8000,
            safety_margin_mb=768,
        )
        self.assertEqual(result["max_ok_mb"], 0)
        self.assertEqual(result["min_failed_mb"], 128)
        failed = [a for a in result["attempts"] if a["status"] == "failed"]
        self.assertTrue(all("fake OOM" in a["error"] for a in failed))

    def test_unknown_available_probes_everything(self):
        result = run_probe(
            fail_above(2048),
            ATTEMPT_SIZES_MB,
            available_mb=None,
            safety_margin_mb=768,
        )
        self.assertEqual(result["max_ok_mb"], 2048)
        self.assertEqual(result["skipped_safety_mb"], [])


if __name__ == "__main__":
    unittest.main()
