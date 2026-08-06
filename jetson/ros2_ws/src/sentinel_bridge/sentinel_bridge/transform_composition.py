"""ROS 메시지에 의존하지 않는 3차원 rigid transform 합성 함수."""

from __future__ import annotations

import math


Vector3 = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]


def multiply_quaternions(left: Quaternion, right: Quaternion) -> Quaternion:
    """Hamilton product ``left * right``를 단위 quaternion으로 반환한다."""
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    result = (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )
    norm = math.sqrt(sum(component * component for component in result))
    if norm == 0.0:
        raise ValueError('cannot normalize a zero quaternion')
    return tuple(component / norm for component in result)  # type: ignore[return-value]


def rotate_vector(vector: Vector3, rotation: Quaternion) -> Vector3:
    """단위 quaternion ``rotation``으로 vector를 회전한다."""
    vx, vy, vz = vector
    qx, qy, qz, qw = rotation

    # q * v * conjugate(q)를 전개한 식이다. TF에서 받은 quaternion은 단위
    # quaternion이지만 수치 오차에 영향받지 않도록 여기서도 정규화한다.
    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if norm == 0.0:
        raise ValueError('cannot rotate with a zero quaternion')
    qx, qy, qz, qw = (value / norm for value in rotation)

    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def compose_transforms(
    parent_translation: Vector3,
    parent_rotation: Quaternion,
    child_translation: Vector3,
    child_rotation: Quaternion,
) -> tuple[Vector3, Quaternion]:
    """``parent→middle``과 ``middle→child``를 ``parent→child``로 합성한다."""
    rotated_child = rotate_vector(child_translation, parent_rotation)
    translation = tuple(
        parent + child
        for parent, child in zip(parent_translation, rotated_child)
    )
    rotation = multiply_quaternions(parent_rotation, child_rotation)
    return translation, rotation  # type: ignore[return-value]
