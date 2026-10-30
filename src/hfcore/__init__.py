"""higgsflow core: catalog, API client, projects, requests, uploads, downloads.

Knows nothing about the terminal: returns Pydantic models and reports progress
through callbacks, so the stage 2 web server can reuse it unchanged.
"""

from importlib.metadata import version

__version__ = version("higgsflow")
