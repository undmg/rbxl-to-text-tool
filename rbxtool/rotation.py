"""Roblox "basic rotation" ids: compact codes for the 24 axis-aligned rotations.

id - 1 == 6 * normal_id(column 0) + normal_id(column 1); column 2 is their cross product.
Normal ids: 0 +X, 1 +Y, 2 +Z, 3 -X, 4 -Y, 5 -Z.
"""

from typing import Tuple

from .values import IDENTITY

_NORMALS = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (-1, 0, 0), (0, -1, 0), (0, 0, -1)]


def basic_rotation(rot_id: int) -> Tuple[float, ...]:
    """Row-major 3x3 matrix for a basic rotation id (identity for unknown ids)."""
    n = rot_id - 1
    if n < 0 or n >= 36:
        return IDENTITY
    c0 = _NORMALS[n // 6]
    c1 = _NORMALS[n % 6]
    if any(a != 0 and b != 0 for a, b in zip(c0, c1)):
        return IDENTITY  # same axis twice: not a valid rotation
    c2 = (
        c0[1] * c1[2] - c0[2] * c1[1],
        c0[2] * c1[0] - c0[0] * c1[2],
        c0[0] * c1[1] - c0[1] * c1[0],
    )
    return tuple(float(v) for v in (c0[0], c1[0], c2[0], c0[1], c1[1], c2[1], c0[2], c1[2], c2[2]))
