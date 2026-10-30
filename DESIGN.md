# higgsflow — a CLI for the Higgsfield API: final design

Repository: `higgsflow`. Command: `higgs`. Unofficial client, not affiliated with Higgsfield.

The directory `~/startups/fable5/higgsflow-web` has nothing to do with this project: it is not its
web part (including not stage 2); do not use or modify code or documentation from there.

Status: design agreed on 2026-10-30, implementation not started.
API documentation: https://docs.higgsfield.ai/docs (full dump: `/docs/llms-full.txt`).

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
  presets, Marketing Studio presets), Ads Studio.
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
  - `hfcore` — the core: catalog, API client, projects, jobs, uploads, downloads. Knows nothing
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
    (so relative paths work) and `~/.config/higgs/` read-only (keys).
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
    ├── jobs/           <request_id>.json — written as soon as a job is submitted
    └── uploads.json    upload cache: file sha256 → public_url + expiry (7 days)
```

**No database.** The source of truth is JSON files:
- on job submission, `.hf/jobs/<request_id>.json` is created immediately (the ID survives a dropped connection);
- after download, next to the file — `<name>.mp4.json`: endpoint, all parameters, prompt,
  input files/references, request_id, price, links, timestamps;
- `list`/`status` scan the files. An index on top can be added later (e.g. for the web UI).

Global: `~/.config/higgs/` — credentials, config (projects root), `models.local.toml`.

**Generation naming:**
`YYYYMMDD-HHMM_<model-slug>_<mode>_<slug>_vNN.<ext>`,
e.g. `20261030-1412_kling-3-0_i2v_sunset-beach_v02.mp4`.
`<slug>` — from `--name` or from the first words of the prompt; `vNN` — version number on collision.

## 6. Model catalog

- The docs have no endpoint listing models. The catalog is parsed from the JSON schemas in
  `docs.higgsfield.ai/docs/llms-full.txt`, stored in the repo as `catalog.json`,
  and updated by `higgs models sync`.
- Per endpoint: id (= path = model string in the SDK), family, mode, parameters
  (types, enum, min/max, default, required), prompt constraints (length, negative prompt,
  tokens like `<<<image_1>>>`), an approximate "from $/sec" price from the pricing page.
- Every request is validated against the schema before submission.
- **Models not in the docs** (e.g. Kling 4.0, Genjutsu Light from the pricing page):
  1. raw call `higgs gen <endpoint-id> -p k=v --no-validate` — works for any path,
     since the API is uniform (`POST /{endpoint}` → `request_id` → the same polling);
     the ID cannot be guessed from the marketing name — take it from the Console/docs/pricing;
     a wrong ID → 404 with no charge;
  2. a manual description in `~/.config/higgs/models.local.toml` (path, parameters, alias) —
     the model shows up in `models list` and in the convenience commands with validation;
  3. `models sync` tries the undocumented `GET /models` with the key and merges the result
     if it returns anything (the code tolerates any response format).

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
| `higgs status [<id>]` / `higgs list [filters]` | job status / project history |
| `higgs sync` | download completed jobs |
| `higgs download <id>` | re-download a result (while the link is alive) |
| `higgs cancel <id>` / `higgs delete <id>` | cancel (queued only) / delete on the API side |
| `higgs pick <generation>` | copy to `final/` |
| `higgs ref save <generation> <name>` | save a generation as a reference |
| `higgs usage` | project spend (local) + account summary (`GET /models/usage`) |

Common convenience-layer flags: `--model`, `--quality`, `--duration`, `--aspect`, `--image`,
`--end-image`, `--ref`, `--video`, `--audio`, `--prompt` / `--prompt-file`, `--name`,
`--seed`, `--yes`, `--no-wait`, `--json`. The CLI picks the endpoint from model + mode
and maps the common flags to the specific model's parameters (`resolution` / `mode=std|pro|4k`, etc.).

Style: everything is set via flags; if a required parameter is missing — an interactive prompt
(choice of model, mode, quality, duration). `--yes` / `--no-input` — for scripts.

## 8. Generation lifecycle

1. Validate parameters against the catalog schema.
2. Local files (`inputs/a.jpg`, `@hero`) are uploaded automatically:
   `POST /files/generate-upload-url` → `PUT` with `upload_headers` → `public_url`.
   Cached by sha256 for 7 days (the API deletes uploads after 7 days).
3. Exact price: `POST /estimate/{endpoint}` with the same body → `{"usd": "..."}`.
   Display, confirmation (`--yes` skips it), project `budget` check (warning when exceeded).
4. Submit `POST /{endpoint}` with an `Idempotency-Key` (UUID); immediately write `.hf/jobs/<request_id>.json`.
5. Polling `GET /requests/{id}/status`: 2 s → backoff up to 10 s + jitter.
   `429 account_queue_full` → wait for `Retry-After`; `503` → retry with backoff.
6. Terminal statuses: `completed`, `failed`, `nsfw`, `canceled` (failed/nsfw/canceled are not billed).
7. Auto-download of the result (links live ~7 days) + write the `.json` next to it.
8. `--no-wait`: return the request_id immediately; `higgs sync` downloads it later.

## 9. API reference (as of 2026-10-30)

- Base URL `https://api.higgsfield.ai`. Responses include `X-Correlation-ID`.
- Submit → `{status:"queued", request_id, status_url, cancel_url, expires_after}`.
- Output: `video:{url}`, `images:[{url}]`, `audio:{url}`; some operations have extra artifacts.
- Cancel: `POST /requests/{id}/cancel` (202, or 400 if already in progress).
  Delete: `DELETE /requests/{id}` (409 while queued/in_progress).
- Limits are by concurrency; jobs beyond the limit are queued, queue overflow → 429.
- Billing in USD (separate from higgsfield.ai subscription credits), video — per second of output.
  There is no balance endpoint (Console only). There is no generation history endpoint.
- Errors: 401 invalid key; 403 `api_key_expired`/`api_key_disabled`/insufficient funds;
  404/423/503 — model unavailable; 422 — idempotency conflict.
- Allowed upload types: jpeg/png/webp/gif, wav, mp4.

**Unconfirmed:** the format and purpose of `GET /models`; the actual default concurrency
(2 or 20); exact prices for all configurations (other than `/estimate`); global file size limits;
the Kling 4.0 and Genjutsu Light endpoints.

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
