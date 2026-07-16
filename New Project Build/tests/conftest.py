import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ddt_mirror.core.xsy_parser import parse_xsy  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.xsy")


@pytest.fixture(scope="session")
def sample_xml() -> str:
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="session")
def parsed(sample_xml):
    return parse_xsy(sample_xml)
