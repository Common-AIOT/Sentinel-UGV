import unittest

from evaluation.llm_noise_recovery_bench import (
    _is_risk_to_safe,
    select_observations,
)


class LlmNoiseRecoveryBenchTest(unittest.TestCase):
    def test_selection_keeps_requested_order(self):
        raw = {
            "observations": [
                {
                    "case_id": "domain-04--moto--0db",
                    "condition": "moto@0db",
                    "run": 1,
                    "ok": True,
                },
                {
                    "case_id": "domain-04--clean",
                    "condition": "clean",
                    "run": 1,
                    "ok": True,
                },
            ]
        }
        selected = select_observations(
            raw, conditions=("clean", "moto@0db"), source_cases=("domain-04",)
        )
        self.assertEqual(["clean", "moto@0db"], [row["condition"] for row in selected])

    def test_risk_to_safe_only_counts_dangerous_direction(self):
        self.assertTrue(_is_risk_to_safe("mobilityStatus", "NO", "YES"))
        self.assertTrue(_is_risk_to_safe("urgentConditionReported", "YES", "NO"))
        self.assertFalse(_is_risk_to_safe("mobilityStatus", "NO", "UNKNOWN"))
        self.assertFalse(_is_risk_to_safe("urgentConditionReported", "NO", "YES"))


if __name__ == "__main__":
    unittest.main()
