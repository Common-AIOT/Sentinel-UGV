import math
import unittest

from sentinel_bridge.transform_composition import compose_transforms


def yaw_quaternion(degrees: float) -> tuple[float, float, float, float]:
    half_angle = math.radians(degrees) / 2.0
    return 0.0, 0.0, math.sin(half_angle), math.cos(half_angle)


class TransformCompositionTest(unittest.TestCase):
    def assert_vector_almost_equal(
        self,
        actual: tuple[float, ...],
        expected: tuple[float, ...],
    ) -> None:
        self.assertEqual(len(actual), len(expected))
        for actual_value, expected_value in zip(actual, expected):
            self.assertAlmostEqual(actual_value, expected_value, places=9)

    def test_compose_transforms_keeps_identity_parent(self) -> None:
        translation, rotation = compose_transforms(
            (0.0, 0.0, 0.0),
            yaw_quaternion(0.0),
            (1.0, 2.0, 0.0),
            yaw_quaternion(-90.0),
        )

        self.assert_vector_almost_equal(translation, (1.0, 2.0, 0.0))
        self.assert_vector_almost_equal(rotation, yaw_quaternion(-90.0))

    def test_compose_transforms_rotates_child_translation_and_yaw(self) -> None:
        translation, rotation = compose_transforms(
            (10.0, 20.0, 0.0),
            yaw_quaternion(90.0),
            (2.0, 0.0, 0.0),
            yaw_quaternion(-30.0),
        )

        self.assert_vector_almost_equal(translation, (10.0, 22.0, 0.0))
        self.assert_vector_almost_equal(rotation, yaw_quaternion(60.0))

    def test_compose_transforms_rejects_zero_quaternion(self) -> None:
        with self.assertRaisesRegex(ValueError, 'zero quaternion'):
            compose_transforms(
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                yaw_quaternion(0.0),
            )


if __name__ == '__main__':
    unittest.main()
