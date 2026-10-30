# higgsflow

`higgs` — an unofficial local CLI for the [Higgsfield API](https://docs.higgsfield.ai/docs), not affiliated
with Higgsfield. Every piece of work lives in a self-contained Project folder. See `DESIGN.md` for the
design, `GLOSSARY.md` for the domain terms and `docs/adr/` for the decisions behind them.

## Install

`higgs` runs inside a Docker container; the host only gets a thin wrapper script. It needs
**Docker Desktop** (ADR 0003: the wrapper always calls `/usr/local/bin/docker --context desktop-linux`,
never whatever `docker` is first in `PATH`).

```sh
ln -s "$PWD/bin/higgs" ~/.local/bin/higgs   # ~/.local/bin must be in PATH
higgs --version
```

The first call builds the image. The image tag is a hash of the source tree, so any change to the code
triggers a rebuild on the next call, and an unchanged tree reuses the existing image.

The wrapper mounts, at identical absolute paths:

- the projects root (`~/hf-projects` by default; override with `HIGGS_PROJECTS_ROOT` or
  `projects_root = "..."` in `~/.config/higgs/config.toml`);
- `~/.config/higgs/` (credentials, config, user catalog), read-write.

Only the projects root is visible inside the container, so files you pass to `higgs` must live in a Project.
The wrapper also passes the host's time zone (`TZ`, from `$TZ` or the `/etc/localtime` symlink, else UTC),
so the times in Generation names are local.

## Usage

```sh
higgs auth login                 # saves KEY_ID:KEY_SECRET to ~/.config/higgs/credentials.toml (mode 600)
higgs init demo                  # creates ~/hf-projects/demo with the standard layout
higgs projects                   # lists the Projects under the projects root
higgs models list --mode i2v     # Models with their Family, Modes, quality tiers and "from" price
higgs models show kling-3.0      # every Endpoint of a Model: parameters, allowed values, prompt limits
higgs models sync                # refresh the catalog from the live docs into ~/.config/higgs/catalog.json
cd ~/hf-projects/demo
higgs video t2v --model kling-3.0 --quality pro --duration 5 --aspect 16:9 --prompt "Waves at dusk"
                                 # picks the Endpoint from Model + Mode (+ tier) and maps the flags onto it
higgs video i2v -m kling-o3 -q pro --image inputs/first.jpg --end-image @hero --prompt-file prompts/turn.md
higgs video t2v -m wan-2.7 --prompt "Waves at dusk" --count 3 --seed 7   # 3 Requests, seeds 7, 8, 9
higgs video ref -m seedance-2.0 --ref @hero --ref inputs/sofa.png --audio inputs/voice.wav \
  --prompt "The hero sits on the sofa and sings"     # references: images (--ref), --video, --audio
higgs video edit -m kling-o3 --video inputs/clip.mp4 --ref @hat --prompt "Give him the hat"
higgs video motion -m kling-3.0-motion-control -q pro --video inputs/dance.mp4 --image @hero
higgs image t2i -m soul-v2 -q 1080p --aspect 3:4 --prompt "Editorial portrait" -p batch_size=4
                                 # one Request, four Generations: ..._v01-1.png ... _v01-4.png
higgs image t2i -m grok-image-2.0 --image @hero --image inputs/style.png --prompt "Hero in this style"
higgs image i2i -m qwen-image-3 --image inputs/street.jpg --prompt "Make it night, wet asphalt"
higgs gen higgsfield-ai/soul/v2/standard -p prompt="Editorial portrait" -p batch_size=4
                                 # validates, submits a Request, waits for it and downloads the Generations
higgs gen bytedance/seedance-2.0/text-to-video -p prompt="Waves at dusk" --name sunset-beach \
  --no-wait                      # only submits; the record lands in .hf/requests/<request_id>.json
higgs status [<request-id>]      # refreshes one Request, or every pending one, from the API
higgs sync                       # checks every Request not downloaded yet once; downloads the completed ones
higgs download <request-id>      # downloads a completed Request's Generations again, while its links are alive
higgs list --status pending --model kling --mode i2v --since 2026-10-01   # history from local files only
higgs pick generations/2026-10-30/<file>.png
                                 # copies the Generation, its .json and Artifacts into final/
higgs usage                      # the Project's Spend and Budget + the account's last 30 days (GET /models/usage)
```

`status` never downloads; `sync` does, without waiting: each Request not downloaded yet gets one status
check, a completed one is downloaded exactly once (its record gets `downloaded_at`), a failed, nsfw or
canceled one is recorded and left alone, and one whose links have expired is reported without an API
call. If a Request cannot be synced, the others still are and `sync` exits with an error; run it again.
`download` fails before any API call once the Request's `expires_at` has passed or it was deleted with
`higgs delete`. `list` reads only `.hf/requests/` (no API calls, no key needed); `--status` is repeatable
(`pending` = not finished yet), `--date` picks one day. `--model` takes a Model as `higgs models list` shows
it (`kling-3.0` or `"Kling 3.0"`: every tier and Mode of that Model, not Kling 3.0 Turbo) and also matches
any Request whose Endpoint id or model slug contains the text (`--model kling-video`, `--model soul`).

Without `--no-wait`, `higgs gen` polls the Request (2 s backing off to 10 s, waiting out a full queue and
retrying transient errors) and downloads every Generation and Artifact into `generations/YYYY-MM-DD/` as
`YYYYMMDD-HHMM_<model-slug>_<mode>_<slug>_vNN[-N].<ext>`, each with a `<file>.json` describing how it was
made. `<slug>` is `--name` or the first words of the prompt; `vNN` counts the Project's Requests with that
slug. The model slug and Mode come from the catalog, with the quality tier when the Endpoint has one
(`kling-video/v3.0/pro/image-to-video` → `kling-3-0-pro_i2v`); for an Endpoint the catalog lacks they come
from the Endpoint id (see DESIGN.md §5). A failed, nsfw
or canceled Request ends with an error and downloads nothing.

### Video: `higgs video t2v|i2v|ref|vref|edit|extend|motion|restyle`

`higgs video <mode>` is `higgs gen` with friendly flags: `--model` (slug or name, as `higgs models list`
shows) and the Mode pick the one Endpoint, and the common flags are mapped onto that Endpoint's own
parameters, so the same flag means the same thing for every Model. Each Mode is its own subcommand
(`higgs models list --mode edit` shows its Models):

| Mode | What it does | Its inputs |
|---|---|---|
| `t2v` | text to video | `--prompt` |
| `i2v` | animate a start image (towards an end image) | `--image`, `--end-image` |
| `ref` | a new video with the people and things of the references | `--ref` images (repeatable), `--video`, `--audio`, `--image`/`--end-image` where the Model has frames |
| `vref` | a new video after a reference video (Kling O3/Omni) | `--video`, `--ref` |
| `edit` | edit a video as the prompt says (Kling O3/Omni, Seedance 2.5, Genjutsu object swap) | `--video`, `--ref`, `--audio` |
| `extend` | continue a video (Seedance 2.5) | `--video`, `--ref`, `--audio` |
| `motion` | the character of an image moves like the one in a video (Kling Motion Control, Genjutsu motion transfer) | `--video`, `--image` |
| `restyle` | restyle a video with a Preset (Genjutsu restyle) | `--video`, `--ref`, `-p preset_id=<uuid>` |

| Flag | Parameter (first one the Endpoint has) |
|---|---|
| `--prompt` / `--prompt-file` | `prompt` |
| `--image` | `image_url`, `first_frame_url`, `start_image_url`; in `motion` also `image_urls` (Genjutsu) |
| `--end-image` | `end_image_url`, `last_image_url`, `last_frame_url`, `tail_image_url` |
| `--ref` (repeatable) | `image_urls`, in the order given (Cinema Studio's `<<<image_1>>>` is the first `--ref`) |
| `--video` | `video_url`, else `video_urls` (a list of one) |
| `--audio` | `audio_url`, else `audio_urls` (a list of one) |
| `--duration` | `duration` (as the schema's type: `5`, or `"5"` where it is a string) |
| `--aspect` | `aspect_ratio` |
| `--quality` | the quality tier (`std`/`pro`/`4k`/`fast`) for Kling 2.5 Turbo, Kling 3.0 and LTX-2.5; otherwise `mode` (Kling O3/Omni), `resolution` (`720p`, `1080p`, ...) |
| `--seed` | `seed` |

Allowed values match case-insensitively (`--quality 2k` sends MiniMax H3's `"2K"`). A flag the Endpoint has no
parameter for fails before anything is sent (`--end-image is not supported by kling-2.6 i2v`, code
`unsupported_flag`); anything else goes in `-p key=value` as with `higgs gen`, applied last so it wins over the
flags (Kling Motion Control's `-p character_orientation=image`, Seedance's `-p generate_audio=false`, several
reference videos as `-p 'video_urls=["inputs/a.mp4","inputs/b.mp4"]'`). `--image`, `--end-image`, `--ref`,
`--video` and `--audio` take a file in the Project or a Reference (`@hero`), uploaded like any Input. Prompt
tokens are checked against what was given: Cinema Studio 4.0 with two `--ref` and `<<<image_3>>>` in the prompt
fails `validation_failed` before anything is uploaded. Genjutsu restyle needs a Preset id as
`-p preset_id=<uuid>` (`--preset <name>` is still to come); without one it fails validation.
`--prompt-file prompts/x.md` reads the prompt from a file inside the Project (relative to the current
directory). The body is then validated, priced, confirmed and submitted exactly like `higgs gen`'s, and
`--name`, `--no-wait`, `--yes`, `--no-input`, `--json` and `--project` work the same way.

What is not given comes from `project.toml`; `[defaults.video]` wins over the shared `[defaults]` (which
`higgs image` shares, with its own `[defaults.image]`). A default the Endpoint does not accept is skipped, not an error (e.g. `aspect` for
Hailuo 2.3, which has no aspect ratio, or `duration = 5` for a Model of 6 or 10 seconds), and a default Model
without the Mode counts as no default:

```toml
[defaults]
model = "kling-3.0"   # --model
quality = "pro"       # --quality: a tier, or a resolution/mode value
duration = 5          # --duration
aspect = "16:9"       # --aspect

[defaults.video]
model = "seedance-2.0"
```

Still missing a Model, a quality tier (for a Model with tiers), the input the Mode is about (t2v: the prompt;
i2v: the start image; vref, edit, extend, restyle: the video; motion: the video and the image) or a parameter
the Endpoint requires (LTX-2.5's `duration`, Genjutsu object swap's `--ref`, Wan 2.6 ref's `--video`)? It is asked for, with the allowed choices; with
`--no-input` or `--json` the command fails at once with `missing_param` (`Missing --duration`).

`--count N` submits N separate Requests with the same parameters: every one is estimated, their total is
confirmed once (`Submit 3 Requests for $0.282?`), and they get consecutive `vNN` (`v04`, `v05`, `v06`). Where
the Endpoint has a `seed`, each Request gets its own: `--seed S` gives S, S+1, S+2, ..., otherwise distinct
random seeds in the seed's range (a single Request without `--seed` sends none). A Model's own `batch_size`
stays an ordinary `-p` parameter. Without `--no-wait` every Request is waited for and downloaded; one that
fails fails the command after the others are downloaded. `--json` always prints a list, one object per Request
(`request_id`, `path`, `record`, `generations` once downloaded, `warnings`), even for one Request.

### Images: `higgs image t2i|i2i`

`higgs image <mode>` works like `higgs video <mode>`, with the same `--model`, `--prompt`/`--prompt-file`,
`--aspect`, `--quality`, `--seed`, `--name`, `--count`, `-p`, `--no-wait`, `--yes`, `--no-input`, `--json` and
`--project`. `t2i` covers every text-to-image Model (SOUL, SOUL V2, SOUL Cinema, Marketing Studio Image,
Grok Image 2.0, Recraft V4.1, Qwen Image 3, Ideogram v4, Z-Image Turbo), including those that also take
reference images; `i2i` is Qwen Image 3 edit. Ads Studio is only reachable with `higgs gen`.

| Flag | Parameter (first one the Endpoint has) |
|---|---|
| `--image` (repeatable) | `image_urls` (Grok, Marketing Studio, Qwen edit: every `--image`), `image_url` (Ideogram), `image_reference_url` (SOUL): one image |
| `--aspect` | `aspect_ratio` |
| `--quality` | `resolution` (`1k`, `2k`, `4k`, `720p`, `1080p`), `quality` (`low` … `max`), `rendering_speed` (Ideogram: `turbo`, `default`, `quality`), whichever allows the value |
| `--seed` | `seed` (SOUL, Qwen Image 3, Z-Image Turbo) |

`--image` takes a file in the Project or a Reference (`@hero`), uploaded like any Input; giving it twice to a
Model that takes one image fails before anything is sent (`too_many_values`). Grok and Marketing Studio
have both `quality` and `resolution`: `--quality` sets the one whose values match, the other goes in `-p`
(`-q high -p resolution=4k`). `[defaults.image]` wins over `[defaults]`:

```toml
[defaults.image]
model = "soul-v2"
quality = "1080p"
aspect = "3:4"
```

A Model's own `batch_size` (SOUL: 1 or 4) is an ordinary `-p` parameter: one Request whose Generations are
numbered `..._v01-1.png`, `..._v01-2.png`, ... . It combines with `--count`: `--count 2 -p batch_size=4`
submits two Requests (`v01`, `v02`, one price confirmation), each with four Generations (`v01-1` … `v02-4`).

`higgs pick <generation>` takes a path or just the file name (looked up under `generations/`) and copies
the Generation with its `.json` and all its Artifacts into `final/` under the same names, as real copies
(no symlinks). Picking the same Generation again refreshes the copies; a file without its `.json` is not a
Generation and is refused.

The key can also come from `HF_KEY="id:secret"` or `HF_API_KEY` + `HF_API_SECRET`, which take precedence
over the saved file. Project-scoped commands find the Project by walking up from the current directory to
the nearest `project.toml`, like git; `--project <name>` picks another one. `-p` values are parsed as JSON
when they are valid JSON (`5`, `0.5`, `true`, `null`, `[...]`, `{...}`) and are strings otherwise
(`16:9`); quote a value to force a string (`-p 'seed="42"'`). Every command takes `--json`.

A `-p` value that is a local file is an Input: it is uploaded before the submit and replaced by its
`public_url`, also inside lists and objects (`-p image_url=inputs/a.jpg`,
`-p 'images=["inputs/a.jpg","inputs/b.png"]'`). A string counts as a file when it names an existing file
(relative to the current directory, or absolute) or ends in `.jpg`/`.jpeg`/`.png`/`.webp`/`.gif`/`.wav`/`.mp4`
(then a missing file is an error); URLs are left alone. The file must be inside the projects root (copy it
into the Project's `inputs/` first) and really be a jpeg, png, webp, gif, wav or mp4, judged by its content.
Uploads are cached by content in `.hf/uploads.json` and reused for up to 7 days, and the Request record lists
the Inputs it used.

A value `@name` is a Reference: the file `references/<name>.<ext>`, uploaded like any Input
(`-p image_url=@hero`, also inside lists and objects). Any file in `references/` is a Reference (its `.json`
metadata is not), and a name must be unique regardless of extension: `@hero` with both `hero.png` and
`hero.mp4` fails and lists them, and an unknown name fails and lists the Project's References. To send a
literal string starting with `@`, double it: `-p style=@@hero` sends `@hero` (text with spaces such as
`-p "prompt=@hero walks in"` is never a Reference).

```sh
higgs ref save generations/2026-10-30/<file>.png hero   # copies the Generation + its .json to references/hero.png
higgs ref save <file>.png hero --force                  # a bare Generation file name works too; --force replaces
                                                        # an existing @hero (any extension) and its .json
higgs ref list                                          # References with type, size and source Generation
```

`higgs gen` validates the parameters against the Endpoint in the catalog before anything is uploaded or sent:
required parameters, types, allowed values, ranges, string lengths, array sizes, the schema's conditional rules,
parameters the Endpoint does not have, and prompt tokens such as `<<<image_2>>>` (which need a second item in
`image_urls`). Every problem is reported at once; with `--json` the error carries a list of
`{"param", "constraint", "message"}`, where `param` is a path such as `multi_prompt[0].prompt` and `constraint` is
the JSON Schema keyword that failed (`required`, `type`, `enum`, `maximum`, `maxLength`, `maxItems`, `format`, …),
`unknown` or `token`:

```json
{"error": {"code": "validation_failed", "message": "Invalid parameters for kling-video/v3.0/pro/image-to-video: ...",
  "problems": [{"param": "duration", "constraint": "maximum", "message": "must be at most 15; got 20"}]}}
```

A local file path stands in for the URL it will be uploaded to, so `-p image_url=inputs/a.jpg` is valid.
`--no-validate` skips the check and submits the parameters as they are, to any path, even one missing from the
catalog (a wrong id is a free 404). An Endpoint id that is not in the catalog fails without `--no-validate`.
Endpoints that `higgs models sync` found only through `GET /models` (`"source": "api"` in `models show --json`)
have no schema, so they take any parameters without `--no-validate`.

### Models missing from the docs: `models.local.toml`

Describe an Endpoint the docs do not have (yet) in `~/.config/higgs/models.local.toml`; it then shows up in
`higgs models list` / `models show` and `higgs gen` validates against it like against a documented one. Each
`[[endpoint]]` table is one Endpoint, followed by one `[endpoint.parameters.<name>]` table per parameter:

```toml
[[endpoint]]
id = "kling-video/v4.0/pro/image-to-video"   # the API path (take it from the Console; it cannot be guessed)
model = "Kling 4.0"                          # Model name; slug kling-4.0. An existing Model's name joins it
family = "kling"                             # Family id; a new id adds a Family (name it with family_name = "...")
mode = "i2v"                                 # t2v, i2v, ref, vref, edit, extend, motion, restyle, t2i, i2i, ads
# tier = "pro"                               # only when the Model has several quality tiers in this Mode
price = { usd = "0.07", unit = "second" }    # optional "from" price; unit "second" or "image"
notes = ["image_url is the first frame."]    # optional; title and doc_url are optional too

[endpoint.prompt]                            # optional: prompts longer than this are cut by the provider
truncated_after = 2500                       # (tokens = ["<<<image_1>>>"] would allow media tokens)

[endpoint.parameters.image_url]
type = "string"                              # string, integer, number, boolean, array, object
required = true
format = "uri"
description = "The first frame."

[endpoint.parameters.prompt]
type = "string"
required = true
max_length = 2500

[endpoint.parameters.duration]
type = "integer"
default = 5
minimum = 3
maximum = 15

[endpoint.parameters.sound]
type = "string"
enum = ["on", "off"]
default = "on"
```

Parameter keys: `type`, `required`, `default`, `enum`, `minimum`, `maximum`, `multiple_of`, `min_length`,
`max_length`, `min_items`, `max_items`, `item_type` (for arrays), `format`, `nullable`, `description`. Only the
listed parameters are accepted. The prompt's required flag and lengths come from the `prompt` and
`negative_prompt` parameters. An entry whose `id` is a documented Endpoint replaces it as a whole (it is not
merged with the documented one), so it can correct or extend a schema. A malformed file (bad TOML, an unknown
key, an unknown mode, a duplicate id, a second untiered Endpoint for the same Model and Mode) fails every
catalog command with an error naming the file and the problem.

### Price, Spend and Budget

After validation and uploads, `higgs gen` asks the API for the exact price (`POST /estimate/{endpoint}`, free)
of the body it is about to send, also with `--no-validate`, shows it and asks for confirmation; `--yes`
confirms without asking. `--no-input` never prompts, and `--json` implies it, so both fail with
`confirmation_required` (the error carries `price_usd`) unless `--yes` is given. A failed estimate submits
nothing. The price is stored on the Request record (`estimate_usd`) and in each Generation's `.json`
(`price_usd`). A Project's Spend is the sum of the prices of its `completed` Requests. An optional Budget in
USD goes at the top level of `project.toml`:

```toml
name = "demo"
budget = 20.0

[defaults]
```

When Spend plus the new price exceeds it, `higgs gen` prints a warning and still asks (with `--yes` it only
warns); it never blocks. In JSON mode the warning is in the output's `warnings` list as
`{"code": "over_budget", "message": ..., "spend_usd": ..., "price_usd": ..., "budget_usd": ...}`; money is
always a decimal string.

`higgs usage` shows what the Project has cost and what the account has used:

- **Project** (from `.hf/requests/` only, no key needed): the Spend of its completed Requests, the Budget and
  what is left of it (negative once over), and the Spend per Model and Mode. The Model is the catalog's
  (`kling-3.0`, all its tiers together, the same value `higgs list --model` takes); an Endpoint the catalog
  lacks is named as in its Generation names.
- **Account**: the analytics of `GET /models/usage` for the last 30 UTC days up to now, summed per Endpoint
  (quantity, unit, cost) with a USD total. Higgsfield calls it analytics, not a billing record: it can lag and
  differ from the invoice. A response in a shape other than the documented one is shown as it came.
- **Balance**: the API has no balance endpoint; `usage` points to the Console (console.higgsfield.ai).

In a Project, a failing account call (no key, a rejected key, an API or network error) is a warning and
`usage` still shows the Spend and exits 0. Outside a Project (and without `--project`) it shows the account
only, and then a failing account call is an error (exit 1). The `--json` output:

```json
{
  "project": {"name": "demo", "spend_usd": "0.80", "requests": 2, "budget_usd": "2.5", "remaining_usd": "1.70",
              "by_model": [{"model": "kling-3.0", "mode": "i2v", "requests": 1, "spend_usd": "0.50"},
                           {"model": "soul-v2", "mode": "t2i", "requests": 1, "spend_usd": "0.30"}]},
  "account": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-30T12:00:00Z", "timeframe": "day",
              "cost_usd": "11.2800", "raw": null,
              "by_model": [{"model": "higgsfield-ai/soul/v2/standard", "unit": "generation", "quantity": "120",
                            "cost": "11.2800", "currency": "USD"}]},
  "balance_url": "https://console.higgsfield.ai",
  "warnings": []
}
```

`project` is `null` outside a Project; `budget_usd` and `remaining_usd` are `null` without a Budget. `account`
is `null` when the call failed, with a warning
`{"code": "account_usage_unavailable", "message": ..., "error": {"code": "invalid_key", ...}}`; for a response
of unknown shape it has `raw` (the body), an empty `by_model` and `cost_usd: null`.

## Development

Development, type checking and tests run in the same container:

```sh
scripts/dev test               # pytest (extra args are passed through, e.g. scripts/dev test -k version)
scripts/dev typecheck          # mypy --strict
scripts/dev shell              # a shell in the dev container
scripts/dev catalog            # rebuild src/hfcore/catalog/catalog.json from the snapshots in catalog/
```

Users refresh their own catalog with `higgs models sync` (no rebuild needed). To refresh the catalog
shipped in the image, replace the snapshots and rebuild:

```sh
curl -sSfL https://docs.higgsfield.ai/docs/llms-full.txt -o catalog/llms-full.txt
curl -sSfL 'https://dash.higgsfield.ai/api/v2/pricing/models/?page=1&page_size=50' | jq . > catalog/pricing.json
scripts/dev catalog
```

The repo is mounted over `/app`, where the package is installed in editable mode, so source changes
are picked up without a rebuild; changes to `pyproject.toml` or the `Dockerfile` rebuild the image.

Tests follow the seams agreed in the spec (#4): they invoke the `higgs` command in-process with
`CliRunner`, replace HTTP at the transport level with `respx`, and use a temporary projects root and
config dir (`HIGGS_PROJECTS_ROOT`, `HIGGS_CONFIG_DIR`) per test. Internal functions are never mocked.
