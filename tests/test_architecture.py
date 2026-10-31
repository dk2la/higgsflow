import re
import subprocess
import sys
from pathlib import Path

import hfcore


def test_hfcore_does_not_import_terminal_libraries():
    # Imports every hfcore module in a fresh interpreter and checks that neither
    # Typer nor Rich got loaded: the core must stay usable by the stage 2 web server.
    script = """
import importlib, pkgutil, sys
import hfcore
for module in pkgutil.walk_packages(hfcore.__path__, "hfcore."):
    importlib.import_module(module.name)
leaked = sorted(name for name in ("typer", "rich", "click") if name in sys.modules)
print(",".join(leaked))
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)

    assert result.stdout.strip() == ""


def test_hfcore_reads_the_time_only_through_its_clock():
    # `hfcore.clock` is the one source of "now", so a fake clock installed by a test is
    # seen by every timestamp the core writes.
    package = Path(hfcore.__file__).parent
    offenders = [
        f"{path.relative_to(package)}:{number}"
        for path in sorted(package.rglob("*.py"))
        if path.name != "clock.py"
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if re.search(r"\b(datetime\.(now|utcnow|today)|time\.time)\(", line)
    ]

    assert offenders == []
