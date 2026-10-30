import subprocess
import sys


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
