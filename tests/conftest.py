import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # for demo.scenarios

SAMPLE_REPO = ROOT / "sample_ads_platform"


@pytest.fixture
def sample_repo() -> Path:
    return SAMPLE_REPO


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    """A minimal repo with one module and a passing test."""
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    return tmp_path
