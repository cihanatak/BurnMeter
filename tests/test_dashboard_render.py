"""Run the JS multi-device render invariants (tests/dashboard_render.test.mjs) as part of
pytest. The frontend has no JS test runner, so this shells out to Node — which is present
in dev/CI. Skips (not fails) if Node isn't installed, so the Python suite still runs."""
import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_TEST = Path(__file__).parent / "dashboard_render.test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node not installed — skipping JS render test")
def test_dashboard_render_invariants():
    """combined-tab burn == single-tab burn at every window/scope; the live gauge tick
    paints the cross-device aggregate, never local-only (the flicker regression guard)."""
    result = subprocess.run(
        [_NODE, str(_TEST)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, (
        "dashboard_render.test.mjs failed:\n"
        + result.stdout + "\n" + result.stderr
    )
    assert "assertions passed" in result.stdout, result.stdout
