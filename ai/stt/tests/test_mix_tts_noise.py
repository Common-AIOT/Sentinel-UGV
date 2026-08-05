import unittest

import numpy as np
from tools.mix_tts_noise import mix_motor_noise


class TtsNoiseMixTest(unittest.TestCase):
    def test_mix_is_deterministic_and_does_not_clip(self):
        t = np.arange(16000, dtype=np.float32) / 16000
        samples = 0.1 * np.sin(2 * np.pi * 440 * t)
        first = mix_motor_noise(samples, 16000, 5, seed=7)
        second = mix_motor_noise(samples, 16000, 5, seed=7)
        np.testing.assert_array_equal(first, second)
        self.assertLessEqual(float(np.max(np.abs(first))), 0.89)

    def test_silence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "silence"):
            mix_motor_noise(np.zeros(100), 16000, 5)


if __name__ == "__main__":
    unittest.main()
