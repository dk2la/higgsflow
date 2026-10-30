# higgsflow — a CLI for the Higgsfield API: final design

Repository: `higgsflow`. Command: `higgs`. Unofficial client, not affiliated with Higgsfield.

The directory `~/startups/fable5/higgsflow-web` has nothing to do with this project: it is not its
web part (including not stage 2); do not use or modify code or documentation from there.

Status: design agreed on 2026-10-30, implementation not started.
API documentation: https://docs.higgsfield.ai/docs (full dump: `/docs/llms-full.txt`).
Domain terms (Request, Generation, Endpoint, Model, Mode, Reference, …) are defined in `GLOSSARY.md`.

## 1. Goal

A local `higgs` CLI covering the entire documented Higgsfield API, with projects in which
everything (input files, references, prompts, generations, final videos) lives in one place
with a clear structure and naming.

Stage 1 — CLI. Stage 2 — a local web interface (FastAPI + a lightweight frontend) on top of the
same core and the same project folders. Stage 1 has no web code, only an architecture ready for it.

## 2. Scope

**In scope (the entire documented API):**
- Video: 67 endpoints in 22 families — Seedance 2.0/2.5, Kling 2.5 Turbo/2.6/3.0/3.0 Turbo/O3/Omni,
  Kling Motion Control 2.6/3.0, Cinema Studio 4.0, Genjutsu (motion transfer, object swap, restyle),
  Happy Horse 1.0/1.1, Wan 2.6/2.7/3.0/3.0 Prime, LTX-2.5, MiniMax H3, Hailuo 2.3, PixVerse V6,
  Grok Imagine Video 1.5. All modes: text-to-video, image-to-video (start/end frame),
  reference-to-video, video-edit, video-extend, motion control/transfer, restyle, etc.
- Images: 17 endpoints (SOUL/V2/Cinema, Marketing Studio Image, Grok Image 2.0,
  Recraft V4.1, Qwen Image 3, Ideogram v4, Z-Image Turbo, and others) — including for references.
- Soul ID training (`/v1/custom-references`), preset catalogs (Soul styles, Genjutsu Restyle
  presets, Marketing Studio presets), Ads Studio (stage 1: only via the generic `higgs gen`).
- Utilities: file upload, price estimate, status, cancel, delete, usage.

**Out of scope:**
- Prompt generation via Claude (no `prompt`/`--enhance`, no CLAUDE.md/skill in projects).
  The user writes prompts themselves.
- Webhooks (a local CLI has no public HTTPS address) — polling only.
- Local video editing (ffmpeg: trim/concat/frames).
- Legacy endpoints `/v1/speak`, `/v1/image2video/dop`, `/v1/motions` — absent from the current docs.
- Web interface — stage 2.

## 3. Stack and environment

- Python 3.12, Typer (commands), Rich (tables, progress), httpx (HTTP), Pydantic (models/schemas).
- Tests: pytest + respx.
- Two packages:
  - `hfcore` — the core: catalog, API client, projects, requests, uploads, downloads. Knows nothing
    about the terminal, returns Pydantic models, reports progress via a callback. The future
    FastAPI server will call it too.
  - `hfcli` — a thin Typer/Rich layer on top of the core. Every command has `--json`
    (for scripts and for Claude Code).
- The official `higgsfield-client` SDK (0.2.0, pre-1.0) is not required: the API is plain REST,
  and an own httpx client gives full control (idempotency, retry, estimate, upload).
- **Isolated environment — Docker, Docker Desktop only** (`/usr/local/bin/docker --context desktop-linux`).
  Rancher Desktop is not used (its `~/.rd/bin/docker` comes first in PATH — hence the docker path
  and context are specified explicitly).
  - The image is built locally from the `Dockerfile` in the repo.
  - The host only has the `~/.local/bin/higgs` wrapper: it rebuilds the image on changes, runs
    `docker run`, mounts the projects root `~/hf-projects` at the same absolute path
    (so relative paths work) and `~/.config/higgs/` read-write (keys, config, user catalog).
  - Only the projects root is visible inside the container. A file outside it (e.g. `~/Downloads/a.jpg`)
    is rejected with a hint to copy it into the project's `inputs/` first.
  - Development and tests also run in the container.
- Repository: GitHub `dk2la/higgsflow` (`git@github.com:dk2la/higgsflow.git`). Video projects live outside the repo.

## 4. Authentication

- Higgsfield key `KEY_ID:KEY_SECRET` from console.higgsfield.ai.
- Header: `Authorization: Key <KEY_ID>:<KEY_SECRET>`.
- Lookup order: env (`HF_KEY="id:secret"` or `HF_API_KEY` + `HF_API_SECRET` — compatible
  with the official SDKs) → `~/.config/higgs/credentials.toml` (chmod 600), filled in by `higgs auth login`.
- Keychain is not used.

## 5. Projects and storage

The default projects root is `~/hf-projects` (configurable). `higgs init <name>` creates:

```
~/hf-projects/<name>/
├── project.toml        name, defaults (model, aspect ratio, quality…), budget (optional)
├── inputs/             own photos/videos/audio
├── references/         references (own + saved generations); referred to as @name
├── prompts/            user prompts (.md), for --prompt-file
├── generations/
│   └── YYYY-MM-DD/     results + .json metadata next to them
├── final/              picked via `higgs pick`
└── .hf/
    ├── requests/       <request_id>.json — written as soon as a Request is submitted
    └── uploads.json    upload cache: file sha256 → public_url + expiry (7 days)
```

**No database.** The source of truth is JSON files:
- on submission, `.hf/requests/<request_id>.json` is created immediately (the ID survives a dropped connection);
- after download, next to the file — `<name>.mp4.json`: endpoint, all parameters, prompt,
  input files/references, request_id, price, links, timestamps;
- `list`/`status` scan the files. An index on top can be added later (e.g. for the web UI).

Global: `~/.config/higgs/` — credentials, config (projects root), `models.local.toml`,
`catalog.json` (written by `higgs models sync`), `souls.toml` (Soul ID registry: name → id;
global because Soul IDs belong to the account and the API has no endpoint to list them).

**References:** any file in `references/` is a Reference, whether put there by hand or via
`higgs ref save` (which copies the Generation together with its `.json`). A Reference name is unique
regardless of extension (`hero.png` and `hero.mp4` cannot coexist); `ref save` to a taken name fails
unless `--force` (which replaces the old Reference and its `.json`, whatever its extension); an ambiguous
`@name` fails and lists the matches, an unknown one lists the Project's References (close matches first).
The `.json` files and hidden files in `references/` are not References. A parameter value is a Reference
when it is exactly `@name` with no whitespace; a value starting with `@@` is sent with one `@` removed
(`@@hero` → the literal string `@hero`).

**Final:** `higgs pick` copies the Generation together with its `.json` into `final/` under the same
name, so a picked file stays self-contained (no symlinks).
- `<generation>` is a path to a Generation file (relative or absolute) or a bare file name, looked up in
  `generations/*/`. It must be in the Project's `generations/`; a file there without its `.json` is not a
  Generation (e.g. an Artifact or a hand-made file) and fails with `generation_metadata_missing` rather than
  landing in `final/` undocumented. All checks (metadata readable, every listed Artifact present) run before
  anything is copied, so a failed pick leaves `final/` untouched.
- The Generation, every Artifact its `.json` lists and the `.json` are real copies (not symlinks or hard
  links): editing or deleting the originals never changes a Pick.
- Picking again is an idempotent refresh: the files in `final/` are copied over again and the result says
  `already_picked: true`.
- The copying is `hfcore.generations.copy_generation(generation, folder)` (same names, reusable for any folder).

**`project.toml`:** `name`, an optional top-level `budget` (USD, a number ≥ 0, e.g. `budget = 20.0`)
and a `[defaults]` table. Unknown keys are kept; an unreadable file or invalid `budget` fails a paid
command with `invalid_project_config` before anything is submitted.

**Current project:** like git, the CLI walks up from the current directory to the nearest
`project.toml`; `--project <name>` overrides it. There is no global "active project".
Outside a project, project-scoped commands fail with a hint to run `higgs init` or pass `--project`.

**Generation naming:**
`YYYYMMDD-HHMM_<model-slug>_<mode>_<slug>_vNN.<ext>`,
e.g. `20261030-1412_kling-3-0-pro_i2v_sunset-beach_v02.mp4` (Kling 3.0 Pro, `kling-video/v3.0/pro/image-to-video`)
or `20261030-1412_soul-v2_t2i_editorial-portrait_v01.png` (SOUL V2, no tiers).
`<model-slug>` is the catalog's Model slug, slugified (`kling-3.0` → `kling-3-0`), followed by the
Endpoint's quality tier when it has one (`-std`, `-pro`, `-4k`, `-fast`): tiers differ in price and
quality, so two tiers of one idea must be told apart by name. `<mode>` is the Endpoint's Mode from the
catalog.
`<slug>` — from `--name` or from the first words of the prompt; `vNN` — the iteration number of
this idea: a counter of Requests with the same `<slug>` across the whole project, regardless
of date and model (`sunset-beach_v01` on Monday, `sunset-beach_v02` on Tuesday with another model).
`--count 3` is three Requests, i.e. three iterations (`v04`, `v05`, `v06`).
- A Request with several Generations (`batch_size`, `images:[...]`) adds a `-N` suffix:
  `..._sunset-beach_v03-1.png`, `..._v03-2.png`. A single Generation gets no suffix.
- Extra artifacts (`zip`, `mov`, `jsx`, `fbx`, `ply` from some video/3D operations) are always downloaded
  and named after their Generation with their own extension (`..._v03.mp4` + `..._v03.fbx`); on an
  extension clash the output field key is appended (`..._v03.mov` + `..._v03-mov.mov`). The single `.json`
  of the Generation lists all its artifacts, and `pick` copies the Generation with all its artifacts.
  With several Generations the artifacts take the Request's name without `-N` and every Generation's
  `.json` lists them; a Request that returns artifacts only (e.g. a 3D scene) has them as its Generations.
- The model slug, Mode, slug and `vNN` are fixed when the Request is submitted and stored in its record
  (`naming`), so a later `sync`/`download` gives the same names. Timestamps (and the `YYYY-MM-DD` folder)
  are in local time: the container runs in UTC, so `bin/higgs` passes the host's time zone as `TZ=<IANA
  name>` (host `$TZ`, else the `/etc/localtime` symlink — on macOS `/var/db/timezone/zoneinfo/Europe/Moscow`
  → `Europe/Moscow` — else `/etc/timezone`, else `UTC`), and the image installs `tzdata` so the name resolves.
- For an Endpoint the effective catalog does not have (a raw `higgs gen --no-validate` to a Model not in
  the docs yet) they come from the Endpoint id: the first path segment that names a
  Mode is the Mode (`text-to-video` → `t2v`, `image-to-video`/`first-last-frame` → `i2v`,
  `reference-to-video`/`image-reference`/`video-reference` → `ref`, `edit`/`video-edit` → `edit`,
  `video-extend` → `extend`, `motion-control`/`motion-transfer` → `motion`, `text-to-image` → `t2i`;
  none → `gen`), and the remaining segments, slugified, are the model slug:
  `kling-video/v4.0/pro/image-to-video` → `kling-video-v4-0-pro` + `i2v`. Without `--name` or a prompt
  the slug is `untitled`.

## 6. Model catalog

- The docs have no endpoint listing models. The catalog is parsed from the JSON schemas in
  `docs.higgsfield.ai/docs/llms-full.txt`, stored in the repo as `catalog.json` and baked into the image
  as the baseline. `higgs models sync` writes `~/.config/higgs/catalog.json`, which takes precedence
  over the baked-in one when present.
- Hierarchy: Family (Kling) → Model (Kling 3.0) → Model + Mode (i2v) = exactly one Endpoint.
  If a Model has no Endpoint for the requested Mode, the CLI fails and lists the Modes it does have.
- Per endpoint: id (= path = model string in the SDK), family, mode, parameters
  (types, enum, min/max, default, required), prompt constraints (length, negative prompt,
  tokens like `<<<image_1>>>`), an approximate "from $/sec" price from the pricing page.
- Every request is validated against the schema before submission.
- **Build.** Snapshots live in `catalog/`: `llms-full.txt` (the docs) and `pricing.json` (the pricing
  page's data, `dash.higgsfield.ai/api/v2/pricing/models/`). `scripts/dev catalog`
  (`python -m hfcore.catalog.build`) turns them into `src/hfcore/catalog/catalog.json`, the package
  data loaded through `hfcore.catalog.load_catalog(config_dir)`; a test fails if the committed
  `catalog.json` is not the build of the committed snapshots. As of 2026-10-30: 83 generation
  Endpoints (67 video, 16 image; the docs' 17th image entry is Soul ID training, not a catalog
  Endpoint), 38 Models, 18 Families.
- **Identifiers.** Model slug = the docs model name lowercased, non-alphanumerics → `-`, dots kept:
  `kling-3.0`, `kling-3.0-turbo`, `seedance-2.5`, `soul-v2`, `recraft-v4.1-pro`. Family = the brand
  (`kling`, `seedance`, `wan`, `happy-horse`, `soul`, …). A Model is a docs model page, except that
  Kling 3.0 Turbo (own path `kling-video/v3.0-turbo/...`) and the three Marketing Studio Image
  versions (2.0 Alpha, 2.5 Flare, 2.5 Sunburst) are separate Models.
- **Modes.** Video: `t2v`, `i2v` (incl. first/last frame), `ref` (reference-to-video, Kling image
  reference, Cinema Studio), `vref` (Kling O3/Omni video reference), `edit` (video edit, Genjutsu
  object swap), `extend`, `motion` (Kling Motion Control, Genjutsu motion transfer), `restyle`
  (Genjutsu restyle). Image: `t2i` (incl. models that also edit with optional image references),
  `i2i` (Qwen Image 3 edit), `ads` (Ads Studio, reachable via `higgs gen` only).
- **Quality tiers.** Where the docs give one Model several paths for one Mode that differ only in a
  tier segment (`std`/`standard`, `pro`, `4k`, `fast`: Kling 2.5 Turbo, Kling 3.0, Kling Motion
  Control 2.6/3.0, LTX-2.5), the tier is a quality variant of the same Model, not a separate Model:
  Model + Mode + tier = one Endpoint, and `--quality` (#16) picks the tier the way it picks
  `resolution` or Kling O3's `mode` parameter elsewhere. A tier is recorded only when the Model has
  more than one (Kling 2.6 is all `pro`, so it has none).
- **Sync.** `higgs models sync` downloads the same two sources live (every pricing page, following
  `next`), runs the same builder and writes `<config dir>/catalog.json` atomically, then reports the
  Endpoints added and removed compared with the catalog in effect before (`--json`: `added`, `removed`,
  `from_api`, `notes`). A docs download or build failure fails the sync (`catalog_sync_failed`) and
  leaves the previous file untouched; a pricing failure only adds a note and keeps the previous
  catalog's prices. Layers, bottom up: the baked-in catalog → replaced by the user `catalog.json` →
  extended by `models.local.toml`; the sync compares the first two only. A user `catalog.json` that
  cannot be read (hand-edited, or from a higgs version with another format) is ignored with a warning
  on stderr and the baked-in catalog is used until the next sync rewrites it, so no command breaks.
- **Prices** are the pricing page's "from" price per Endpoint (USD per second or per image,
  `exact` when it does not vary); a Model's price is the lowest of its Endpoints. Missing for
  Genjutsu Restyle and SOUL Cinema. The exact price always comes from `/estimate`.
- **Models not in the docs** (e.g. Kling 4.0, Genjutsu Light from the pricing page):
  1. raw call `higgs gen <endpoint-id> -p k=v --no-validate` — works for any path,
     since the API is uniform (`POST /{endpoint}` → `request_id` → the same polling);
     the ID cannot be guessed from the marketing name — take it from the Console/docs/pricing;
     a wrong ID → 404 with no charge;
  2. a manual description in `~/.config/higgs/models.local.toml` (path, parameters, alias) —
     the model shows up in `models list` and in the convenience commands with validation.
     One `[[endpoint]]` table per Endpoint (`id`, `model`, `family`, `mode`, optional `tier`, `price`,
     `notes`, `[endpoint.prompt]` soft limits) with one `[endpoint.parameters.<name>]` table per
     parameter (format in the README). It is layered last in `load_catalog`: new ids add Endpoints (and
     Models/Families), an existing id replaces that Endpoint as a whole. A malformed file is an
     `invalid_models_local` error naming the file;
  3. `models sync` tries the undocumented `GET /models` with the key (skipped quietly without one)
     and merges the result if it returns anything usable. Any error or unknown format is only a
     note. It accepts a list, or an object with a list under `models`/`data`/`results`/`items`/
     `endpoints`, of Endpoint paths or objects holding one (`endpoint_id`, `endpoint`, `path`,
     `model_id`, `id`, `slug`, `model`; anything without a `/` is ignored), with an optional name,
     kind and Mode. An Endpoint the docs lack becomes a minimal Endpoint with `source: "api"`, no
     parameters and an empty schema, so validation lets any `-p` parameters through rather than
     requiring `--no-validate`. Its Model is the item's name (Family from it, like the docs) or else
     the path, and its Mode comes from the path like a docs Endpoint. An item whose kind or Mode
     cannot be told, or whose Model + Mode is already taken, is skipped with a note, not guessed.

## 7. Commands

| Command | Purpose |
|---|---|
| `higgs init <name>` / `higgs projects` | create a project / list projects |
| `higgs auth login` | save the key |
| `higgs models list [--mode i2v] [--family kling]` | models, modes, "from" prices |
| `higgs models show <model>` | parameters, allowed quality/duration/aspect, prompt constraints |
| `higgs models sync` | update the catalog |
| `higgs video t2v\|i2v\|ref\|edit\|extend\|motion ...` | convenience layer for video |
| `higgs image ...` | convenience layer for images |
| `higgs gen <endpoint-id> -p k=v ... [--no-validate]` | generic call to any endpoint |
| `higgs status [<request-id>]` / `higgs list [filters]` | Request status / project history |
| `higgs sync` | download completed Requests |
| `higgs download <request-id>` | re-download a Request's Generations (while the links are alive) |
| `higgs cancel <request-id>` / `higgs delete <request-id>` | cancel (queued only; the record becomes `canceled`) / delete on the API side only (local files stay; the record gets `deleted_at`). Only Requests of the Project: an id without a local record fails with `request_not_found` before any API call |
| `higgs pick <generation>` | copy to `final/` with its `.json` and Artifacts (§5) |
| `higgs ref save <generation> <name> [--force]` | save a generation as a reference |
| `higgs soul train <name> <images...> [--version v1\|v2\|cinema]` | train a Soul ID from project images |
| `higgs soul list` / `higgs soul status <name>` | registered Soul IDs / training status |
| `higgs presets list <soul-styles\|restyle\|marketing>` | preset catalogs |
| `higgs usage` | project spend (local) + account summary (`GET /models/usage`) |

Common convenience-layer flags: `--model`, `--quality`, `--duration`, `--aspect`, `--image`,
`--end-image`, `--ref`, `--video`, `--audio`, `--prompt` / `--prompt-file`, `--name`,
`--seed`, `--soul <name>`, `--preset <name>`, `--count N`, `--yes`, `--no-wait`, `--json`.
The CLI picks the endpoint from model + mode and maps the common flags to the specific model's
parameters (`resolution` / `mode=std|pro|4k`, etc.).

- `--soul <name>` resolves the name via `souls.toml` and checks that the Soul ID version matches the SOUL model.
- Soul ID training is not a Request: it is account-scoped, is not written to the project's `.hf/` and
  does not count toward project spend. `soul train` polls `GET /v1/custom-references/{id}` the same
  way as a generation and supports `--no-wait`; `soul status` checks on it later.
- `--count N` submits N separate Requests with the same parameters (a different seed each, if the model
  supports seeds); the total price is shown and confirmed once. A model's own `batch_size` is not
  affected and is passed as a regular parameter (`-p batch_size=...`).

Style: everything is set via flags; if a required parameter is missing — an interactive prompt
(choice of model, mode, quality, duration). `--yes` / `--no-input` — for scripts.
`--json` implies `--no-input`: a missing parameter fails at once with a JSON error. It does not confirm
the price — that still needs an explicit `--yes`, otherwise it is an error too.

## 8. Generation lifecycle

1. Validate parameters against the catalog schema (`hfcore.validation.validate_params`), on the values as
   given: the Endpoint's `input_schema` via `jsonschema` (incl. `if`/`then`/`else`, `allOf`), plus
   parameters the Endpoint does not have and prompt tokens without attached media. A local file path
   stands in for its future upload URL. All problems are reported at once (`validation_failed`, a list of
   `{param, constraint, message}`); `--no-validate` skips this step, and only then may the Endpoint be
   missing from the catalog. The provider's soft prompt limit (`truncated_after`) is not an error.
2. Local files (`inputs/a.jpg`, `@hero`) are uploaded automatically:
   `POST /files/generate-upload-url` → `PUT` with `upload_headers` → `public_url`.
   Cached by sha256 for 7 days (the API deletes uploads after 7 days); an entry is reused while
   more than an hour of it is left. A string parameter value (also inside lists and objects) is a
   local file when it names an existing file (relative to the current directory, or absolute) or
   ends in an allowed media extension (`.jpg`, `.png`, `.mp4`, …; then a missing file is an error);
   URLs never are. The type is judged by the file's content, not its name. Every file is checked
   (projects root, type) before the first upload. The Request record lists its Inputs
   (`param`, `source`, `path`, `sha256`, `content_type`, `public_url`).
3. Exact price: `POST /estimate/{endpoint}` with the same body → `{"usd": "..."}`.
   Display, confirmation (`--yes` skips it), project `budget` check. Project spend is the sum of the
   estimated prices of `completed` Requests (failed/nsfw/canceled are not billed). If spend + this price
   exceeds `budget`: a warning plus confirmation; with `--yes` only the warning, never a block.
   No price, no submit: a failed estimate is an error. The price is stored on the Request record
   (`estimate_usd`) and in the Generation metadata (`price_usd`); money is a `Decimal`, a string in JSON.
   In JSON mode the over-Budget warning goes into the output's `warnings` list (`code: over_budget`), and a
   missing `--yes` fails with `confirmation_required` carrying `price_usd` and `warnings`. `--count N`
   estimates every Request and confirms their total once.
4. Submit `POST /{endpoint}` with an `Idempotency-Key` (UUID); immediately write `.hf/requests/<request_id>.json`.
   A network failure, timeout or `5xx` (other than a disabled model) and a `429` (after `Retry-After`) are
   retried a few times with the same key, so a retried submit is never billed twice.
5. Polling `GET /requests/{id}/status`: 2 s → backoff up to 10 s + jitter.
   `429 account_queue_full` → wait for `Retry-After`; `503` → retry with backoff.
6. Terminal statuses: `completed`, `failed`, `nsfw`, `canceled` (failed/nsfw/canceled are not billed).
7. Auto-download of the result + write the `.json` next to it. Links stop working at `expires_at`
   from the status response; it is stored in the Request's `.json`, and `download` fails early once it has passed.
8. `--no-wait`: return the request_id immediately; `higgs sync` downloads it later.

## 9. API reference (as of 2026-10-30)

- Base URL `https://api.higgsfield.ai`. Responses include `X-Correlation-ID`.
- Submit → `{status:"queued", request_id, status_url, cancel_url, expires_after}`.
- Output: `video:{url}`, `images:[{url}]`, `audio:{url}`, `audios:[{url}]`; some video/3D operations
  return extra artifacts (`zip`, `mov`, `jsx`, `fbx`, `ply`). A finished status includes `expires_at`.
- In API terms one request may consist of several internal "jobs"; cancel works only while all of them are queued.
- Soul ID: `POST /v1/custom-references` (1–100 images, `model_version` v1/v2/cinema), poll
  `GET /v1/custom-references/{id}`, then pass the id as `custom_reference_id` to a matching SOUL endpoint.
  Account-scoped; no list endpoint; too many trainings at once → 429 with `Retry-After`.
- Cancel: `POST /requests/{id}/cancel` (202, or 400 if already in progress).
  Delete: `DELETE /requests/{id}` (409 while queued/in_progress).
- Limits are by concurrency; requests beyond the limit are queued, queue overflow → 429.
- Billing in USD (separate from higgsfield.ai subscription credits), video — per second of output.
  There is no balance endpoint (Console only). There is no generation history endpoint.
- Usage analytics: `GET /models/usage?start&end&timeframe` (range at most 31 days, buckets `hour`/`day`/`week`/`month`,
  `limit` up to 100 buckets) → `{time_series: [{bucket, results: [{model, unit, quantity, unit_price, cost, currency}]}],
  total, page, limit}`; completed generations only, not a billing record. `higgs usage` asks for the last 30 UTC
  days by day and sums per Endpoint; in a Project a failing call is only a warning next to the local Spend.
- Errors: 401 invalid key; 403 `api_key_expired`/`api_key_disabled`/insufficient funds;
  404/423/503 — model unavailable; 422 — idempotency conflict.
- Allowed upload types: jpeg/png/webp/gif, wav, mp4.

**Unconfirmed:** the format and purpose of `GET /models`; the actual default concurrency
(2 or 20); exact prices for all configurations (other than `/estimate`); global file size limits;
the Kling 4.0 and Genjutsu Light endpoints; the price of Soul ID training.

## 10. Development process

- TDD (red → green → refactor), tests through the public interface: invoking a CLI command
  + replacing HTTP at the transport level (respx). Internal functions are not mocked.
- Response fixtures come from the documentation examples; the catalog parser runs on an `llms-full.txt` snapshot in the repo.
- Tests run in a Docker container.
- The live smoke test (upload → submit → poll → download against the real API) is done by the user.

## 11. Stage 2 (not now)

Local web: FastAPI + a lightweight frontend in the same Docker image, on top of `hfcore` and the same
project folders — a gallery of projects/generations, video previews, forms from the models' JSON schemas,
comparing variants, downloads. If needed — an index (SQLite) on top of the JSON files.
The web UI lives in this same repo; `~/startups/fable5/higgsflow-web` has nothing to do with it.
