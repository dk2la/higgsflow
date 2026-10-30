"""Generation names (DESIGN.md §5).

`generations/YYYY-MM-DD/YYYYMMDD-HHMM_<model-slug>_<mode>_<slug>_vNN[-N].<ext>`

The parts that do not depend on the outputs (`Naming`) are fixed when the Request is
submitted and stored on its record, so `vNN` never changes on a later download.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel

from hfcore.catalog import Catalog
from hfcore.errors import HiggsError

# Endpoint path segments that name a Mode, and the short form used in file names.
MODE_SEGMENTS = {
    "text-to-video": "t2v",
    "image-to-video": "i2v",
    "first-last-frame": "i2v",
    "reference-to-video": "ref",
    "image-reference": "ref",
    "video-reference": "ref",
    "video-edit": "edit",
    "edit": "edit",
    "video-extend": "extend",
    "motion-control": "motion",
    "motion-transfer": "motion",
    "text-to-image": "t2i",
}
# The Mode of an Endpoint whose path names none (e.g. `higgsfield-ai/soul/v2/standard`).
DEFAULT_MODE = "gen"
# The slug when there is neither `--name` nor a prompt.
DEFAULT_SLUG = "untitled"
SLUG_WORDS = 4
SLUG_MAX_LENGTH = 40


class Naming(BaseModel):
    """The output-independent parts of a Request's Generation names."""

    model_slug: str
    mode: str
    slug: str
    version: int


def model_and_mode(endpoint: str, catalog: Catalog | None = None) -> tuple[str, str]:
    """Model slug and Mode of an Endpoint id.

    From the catalog when it has the Endpoint: the Model slug with its tier, if the
    Endpoint has one, and the Endpoint's Mode (`kling-video/v3.0/pro/image-to-video` →
    (`kling-3-0-pro`, `i2v`), `higgsfield-ai/soul/v2/standard` → (`soul-v2`, `t2i`)).
    Otherwise (a raw `--no-validate` call to an Endpoint the catalog lacks) from the path,
    see `path_model_and_mode`.
    """
    if catalog is not None:
        try:
            found = catalog.endpoint(endpoint)
        except HiggsError as error:
            if error.code != "unknown_endpoint":
                raise
        else:
            return slugify("-".join(part for part in (found.model, found.tier) if part)), found.mode
    return path_model_and_mode(endpoint)


def path_model_and_mode(endpoint: str) -> tuple[str, str]:
    """Model slug and Mode of a raw Endpoint id, without the catalog.

    The first path segment that names a Mode (`image-to-video` → `i2v`, see
    `MODE_SEGMENTS`) is the Mode, else `gen`; every other segment, slugified and
    joined, is the model slug: `kling-video/v3.0/pro/image-to-video` →
    (`kling-video-v3-0-pro`, `i2v`), `higgsfield-ai/soul/v2/standard` →
    (`higgsfield-ai-soul-v2-standard`, `gen`).
    """
    segments = [segment for segment in endpoint.strip("/").split("/") if segment]
    mode = next((MODE_SEGMENTS[s] for s in segments if s in MODE_SEGMENTS), None)
    model = [s for s in segments if s not in MODE_SEGMENTS] if mode else segments
    return slugify("-".join(model)) or "endpoint", mode or DEFAULT_MODE


def slugify(text: str) -> str:
    """Lowercase words joined by `-`; letters of any script are kept (`Закат у моря` → `закат-у-моря`)."""
    # `_` separates the parts of a name, so it is a word separator here.
    return "-".join(re.findall(r"[^\W_]+", text.lower()))


def request_slug(name: str | None, prompt: str | None) -> str:
    """`--name` if given, else the first words of the prompt, else `untitled`."""
    if name is not None:
        return slugify(name)[:SLUG_MAX_LENGTH].strip("-") or DEFAULT_SLUG
    words = slugify(prompt or "").split("-")[:SLUG_WORDS]
    return "-".join(words)[:SLUG_MAX_LENGTH].strip("-") or DEFAULT_SLUG


def next_version(taken: Iterable[Naming | None], slug: str) -> int:
    """`vNN` for a new Request: one more than the highest version of this slug so far."""
    return 1 + max((n.version for n in taken if n is not None and n.slug == slug), default=0)


def base_name(naming: Naming, submitted_at: datetime) -> str:
    """`YYYYMMDD-HHMM_<model-slug>_<mode>_<slug>_vNN`, in local time."""
    stamp = submitted_at.astimezone().strftime("%Y%m%d-%H%M")
    return f"{stamp}_{naming.model_slug}_{naming.mode}_{naming.slug}_v{naming.version:02d}"


def day_folder(submitted_at: datetime) -> str:
    """`YYYY-MM-DD` under `generations/`, in local time."""
    return submitted_at.astimezone().strftime("%Y-%m-%d")
