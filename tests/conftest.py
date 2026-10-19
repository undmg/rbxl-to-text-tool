import os

import pytest

import rbxtool

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


@pytest.fixture(params=["rbxl", "rbxlx"])
def v1(request):
    return rbxtool.load(fixture(f"place_v1.{request.param}"))


@pytest.fixture(params=["rbxl", "rbxlx"])
def v2(request):
    return rbxtool.load(fixture(f"place_v2.{request.param}"))
