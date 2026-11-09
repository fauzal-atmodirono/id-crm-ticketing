"""The runtime stage of the fork Dockerfile starts again from the untouched
upstream image and only takes what it COPYs --from=builder. A patch that edits
a server-side file (Rails view, initializer, service) therefore ships only if
the runtime stage copies that file (or a directory containing it). Patch 0086
(app/services/filter_service.rb, FIX-51) silently never shipped because its
COPY line was missing; this test makes that class of miss fail in CI.

Build-only paths (not needed at runtime):
  * app/javascript/  -- compiled by vite into public/vite, which IS copied.
  * spec/            -- RSpec files; tests never run in the runtime image.
"""
import re
from pathlib import Path

FORK = Path(__file__).resolve().parent.parent
DOCKERFILE = FORK / "Dockerfile"
PATCHES = sorted((FORK / "patches").glob("*.patch"))

BUILD_ONLY_PREFIXES = ("app/javascript/", "spec/")


def _runtime_copies():
    text = DOCKERFILE.read_text()
    runtime = text.split("AS runtime", 1)[1]
    dests = []
    for m in re.finditer(r"^COPY\s+--from=builder\s+(\S+)\s+(\S+)", runtime, re.M):
        src = m.group(1)
        assert src.startswith("/app/"), src
        dests.append(src[len("/app/"):].rstrip("/"))
    return dests


def _runtime_patched_paths():
    out = {}
    for p in PATCHES:
        for line in p.read_text().splitlines():
            if line.startswith("+++ b/"):
                path = line[len("+++ b/"):]
                if not path.startswith(BUILD_ONLY_PREFIXES):
                    out.setdefault(path, []).append(p.name)
    return out


def _covered(path, copies):
    return any(path == c or path.startswith(c + "/") for c in copies)


def test_every_server_side_patched_file_is_copied_into_runtime():
    copies = _runtime_copies()
    missing = {
        path: names
        for path, names in _runtime_patched_paths().items()
        if not _covered(path, copies)
    }
    assert not missing, f"patched server-side files the runtime stage never copies: {missing}"


def test_filter_service_is_copied():
    assert "app/services/filter_service.rb" in _runtime_copies()


def test_public_vite_still_copied():
    assert "public/vite" in _runtime_copies()
