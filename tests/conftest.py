import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from test_adversarial_stress import TestHarness

@pytest.fixture
def h():
    return TestHarness()
