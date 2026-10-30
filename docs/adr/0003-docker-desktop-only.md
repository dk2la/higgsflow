# Run only in Docker Desktop, with an explicit docker path

The CLI, its development and its tests run in a container, and the host has only a thin `higgs` wrapper. The wrapper calls `/usr/local/bin/docker --context desktop-linux` explicitly instead of whatever `docker` is first in PATH, because Rancher Desktop is also installed on the development machine and its `~/.rd/bin/docker` shadows Docker Desktop's. Rancher Desktop is deliberately not used; do not "simplify" the wrapper back to a bare `docker`.
