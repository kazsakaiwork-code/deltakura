"""Path resolution for the Deltakura monorepo.

No absolute path is ever written into this package: every location is derived at
runtime from this file's own position or from an environment variable, so the
tree can be cloned anywhere without edits.

Layout (one public monorepo, `kazsakaiwork-code/deltakura`):

    <repo>/crawlers/_lib/paths.py   <- this file
    <repo>/crawlers/                collectors + the shipped clearance matrix
    <repo>/data/published/          the ONLY data that is ever committed
    <repo>/site/  <repo>/mcp/  <repo>/api/

Two data locations, deliberately kept apart:

``PUBLISHED``  ``<repo>/data/published/`` — aggregates the site, the Worker and
               the MCP package are built from. Committed. No record-level
               personal-adjacent data, no publisher originals.

``DATA``       the private collection store: publisher originals, the normalized
               layer, crawl state, lookup tables, run logs. **Never committed.**
               Resolved from ``$DELTAKURA_DATA_DIR``, defaulting to ``../data``
               relative to the repository root, i.e. a directory that sits beside
               the checkout. Set the variable explicitly in CI, or anywhere else
               there is no sibling directory.

The clearance matrix (``crawlers/tos_matrix.csv``) is shipped inside the
repository so the fail-closed gate in ``_lib/tos.py`` works in a standalone
checkout. The maintainers keep a fuller internal survey outside the repository;
``crawlers/tools/sync_tos_matrix.py`` projects it into the shipped copy and
``crawlers/tests/test_politeness.py`` fails if the two drift. In a clone with no
internal survey beside it there is nothing to compare and the check is a no-op.
"""

from __future__ import annotations

import os
from pathlib import Path

# <repo>/crawlers/_lib/paths.py -> parents[0]=_lib, [1]=crawlers, [2]=<repo>
ROOT = Path(__file__).resolve().parents[2]

CRAWLERS = ROOT / "crawlers"
PUBLISHED = ROOT / "data" / "published"

#: `../data` relative to the repository root, unless DELTAKURA_DATA_DIR says otherwise.
DEFAULT_DATA_DIR = ROOT.parent / "data"


def _resolve_data() -> Path:
    env = (os.environ.get("DELTAKURA_DATA_DIR") or "").strip()
    return Path(env).expanduser().resolve() if env else DEFAULT_DATA_DIR


DATA = _resolve_data()

#: Shipped clearance matrix. `$DELTAKURA_TOS_MATRIX` overrides it for a probe run.
TOS_MATRIX = Path(os.environ.get("DELTAKURA_TOS_MATRIX", "").strip() or (CRAWLERS / "tos_matrix.csv"))

#: Where the maintainers' full clearance survey lives, when this checkout sits
#: beside it. Set `$DELTAKURA_TOS_SOURCE` to point at it explicitly; otherwise it
#: is looked for as `tos_matrix.csv` in a directory next to the repository root.
#: `None` in a standalone clone, which is the normal published case — the shipped
#: matrix is then simply the matrix, and the drift check has nothing to compare.
INTERNAL_TOS_MATRIX_ENV = "DELTAKURA_TOS_SOURCE"


def internal_tos_matrix() -> Path | None:
    env = (os.environ.get(INTERNAL_TOS_MATRIX_ENV) or "").strip()
    if env:
        candidate = Path(env).expanduser()
        return candidate if candidate.is_file() else None
    parent = ROOT.parent
    if not parent.is_dir():
        return None
    for candidate in sorted(parent.glob("*/tos_matrix.csv")):
        if candidate.is_file() and ROOT not in candidate.parents:
            return candidate
    return None


def data_dir(*parts: str) -> Path:
    """Return <data>/<parts...> in the private store, creating it if needed."""
    p = DATA.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def published_dir(*parts: str) -> Path:
    """Return <repo>/data/published/<parts...>, creating it if needed."""
    p = PUBLISHED.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_dir(source: str) -> Path:
    """Private per-source state (HTTP cache, cursors). Never published."""
    return data_dir(source, "state")


def rel(path: Path) -> str:
    """A short, portable label for `path`. Never an absolute path.

    Paths inside the repository are shown relative to it; paths inside the
    private store keep the historical `data/...` shape so manifest columns stay
    comparable across an earlier directory move. Anything else degrades to the file name
    rather than leaking a host path into a committed file.
    """
    path = Path(path)
    for base, prefix in ((ROOT, ""), (DATA, "data/")):
        try:
            return prefix + path.resolve().relative_to(base).as_posix()
        except ValueError:
            continue
    return path.name
