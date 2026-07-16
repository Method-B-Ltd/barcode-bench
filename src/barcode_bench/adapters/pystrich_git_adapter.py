"""pyStrich-from-git adapter: the development HEAD, benchmarked beside PyPI.

Identical encoding surface to the ``pystrich`` adapter (it *is* the same
class, inheriting ``supports`` and ``capabilities`` wholesale), so any
divergence between the two columns in a report is the difference between the
released package and the current git HEAD - nothing else. As of pyStrich 0.17
that includes ``datamatrix_rect`` (``symbol_shape="rectangular"``): released
and HEAD both enter it, so there is no longer any symbology this adapter
reaches that the released one does not.

The container installs a wheel built from
https://github.com/mmulqueen/pyStrich.git in a separate builder stage (see
containers/Containerfile): the final image never contains git, the clone or
the build tree, so the weighed stack is the installed package alone, exactly
like the PyPI image. The builder records the commit sha to
``/pystrich-git.sha`` and ``lib_versions`` reports that sha as the version -
a wheel built from git still carries a release-style version string, which
would make provenance lie. Outside the container (host smoke runs) the sha file is absent and the
version reports as ``unknown (host venv)``.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from barcode_bench.adapters.pystrich_adapter import PystrichAdapter

_SHA_FILE = Path("/pystrich-git.sha")


class PystrichGitAdapter(PystrichAdapter):
    id = "pystrich-git"

    def lib_versions(self) -> dict[str, str]:
        if _SHA_FILE.is_file():
            sha = _SHA_FILE.read_text(encoding="ascii").strip()[:12]
        else:
            sha = "unknown (host venv)"
        return {"pystrich@git": sha, "pillow": version("pillow")}


ADAPTER = PystrichGitAdapter()
