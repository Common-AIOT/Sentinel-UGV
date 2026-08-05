import json
import tempfile
import unittest
from pathlib import Path

from evaluation.jetson_resource_bench import (
    parse_tegrastats_line,
    read_mem_available_mb,
    summarize,
    write_outputs,
)


LINE = (
    "07-29-2026 12:00:00 RAM 3501/7620MB (lfb 10x4MB) "
    "SWAP 25/12000MB CPU [10%@729,20%@729,off,30%@729,40%@729,off] "
    "GR3D_FREQ 45% cpu@52.1C gpu@54.0C"
)


class JetsonResourceBenchTest(unittest.TestCase):
    def sample(self, phase="command", ram=3501, available=2000.0):
        line = LINE.replace("RAM 3501", f"RAM {ram}")
        return parse_tegrastats_line(
            line,
            elapsed_s=1.25,
            phase=phase,
            mem_available_mb=available,
        )

    def test_parse_tegrastats_line(self):
        sample = self.sample()
        self.assertIsNotNone(sample)
        self.assertEqual(sample.ram_used_mb, 3501)
        self.assertEqual(sample.ram_total_mb, 7620)
        self.assertEqual(sample.swap_used_mb, 25)
        self.assertEqual(sample.cpu_avg_pct, 25.0)
        self.assertEqual(sample.gpu_pct, 45)
        self.assertEqual(sample.temperature_max_c, 54.0)

    def test_line_without_ram_is_ignored(self):
        self.assertIsNone(
            parse_tegrastats_line("not a sample", elapsed_s=0, phase="baseline")
        )

    def test_mem_available_from_proc_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meminfo"
            path.write_text("MemTotal: 8000000 kB\nMemAvailable: 2097152 kB\n")
            self.assertEqual(read_mem_available_mb(path), 2048.0)

    def test_summary_separates_three_phases(self):
        samples = [
            self.sample("baseline", 2000, 5000),
            self.sample("baseline", 2100, 4900),
            self.sample("command", 3000, 3000),
            self.sample("command", 4200, 1500),
            self.sample("after", 2300, 4700),
        ]
        result = summarize(
            samples,
            label="voice-gms",
            command=["python", "-m", "evaluation.pipeline_bench"],
            command_exit_code=0,
            command_elapsed_s=12.5,
        )
        self.assertEqual(result["ramBaselineMb"], 2050.0)
        self.assertEqual(result["ramPeakMb"], 4200)
        self.assertEqual(result["ramAfterMb"], 2300)
        self.assertEqual(result["memAvailableMinimumMb"], 1500)
        self.assertTrue(result["passed"])

    def test_low_available_ram_fails(self):
        result = summarize(
            [self.sample("command", 7000, 620)],
            label="integration",
            command=["true"],
            command_exit_code=0,
            command_elapsed_s=1,
        )
        self.assertFalse(result["passed"])
        self.assertIn("available_ram_below_1024mb", result["failureReasons"])

    def test_writes_three_evidence_files(self):
        samples = [self.sample()]
        result = summarize(
            samples,
            label="voice-gms",
            command=["true"],
            command_exit_code=0,
            command_elapsed_s=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_outputs(output, samples, result)
            self.assertTrue((output / "tegrastats.log").exists())
            self.assertTrue((output / "resource-samples.csv").exists())
            saved = json.loads((output / "resource-summary.json").read_text())
            self.assertEqual(saved["ramPeakMb"], 3501)


if __name__ == "__main__":
    unittest.main()
