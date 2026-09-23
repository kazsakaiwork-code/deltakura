"""Shared library for Deltakura public-data collectors.

Modules:
  http       polite HTTP layer (robots, per-host delay, conditional requests, caps)
  tos        fail-closed source clearance gate read from crawlers/tos_matrix.csv
  anonymize  fail-closed anonymisation rules R1-R6
  jputil     Japanese-data helpers (wareki dates, prefecture codes, NFKC)
  paths      repository-relative path resolution (no absolute paths in source)
"""

__all__ = ["http", "tos", "anonymize", "jputil", "paths"]

# Console output carries Japanese. On Windows the default console codepage is
# cp932, which mangles it in logs and scheduled-task transcripts.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # not a real TTY / already wrapped
        pass
