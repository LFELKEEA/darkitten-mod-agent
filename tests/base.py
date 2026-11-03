from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TempDataCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._old = os.environ.get("DMA_DATA_DIR")
        os.environ["DMA_DATA_DIR"] = str(self.tmp / "data")

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("DMA_DATA_DIR", None)
        else:
            os.environ["DMA_DATA_DIR"] = self._old
        self._tmp.cleanup()
