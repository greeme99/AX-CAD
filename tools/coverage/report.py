"""Writes tools/coverage/REPORT.md from the three coverage scripts (plan §22 W0)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import activex_coverage
import dotnet_coverage
import lisp_coverage

if __name__ == "__main__":
    parts = [lisp_coverage.report(), activex_coverage.report(), dotnet_coverage.report()]
    text = "\n\n---\n\n".join(parts) + "\n"
    (HERE / "REPORT.md").write_text(text, encoding="utf-8")
    print(text[:400])
