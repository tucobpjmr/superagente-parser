"""
Stub markitdown before any test imports main.py.
The real markitdown chain (pdfminer → cryptography) is broken in CI/container.
"""

import sys
from unittest.mock import MagicMock

_fake_markitdown = MagicMock()
sys.modules.setdefault("markitdown", _fake_markitdown)
