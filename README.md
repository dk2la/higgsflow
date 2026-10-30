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

## Usage

```sh
higgs auth login                 # saves KEY_ID:KEY_SECRET to ~/.config/higgs/credentials.toml (mode 600)
higgs init demo                  # creates ~/hf-projects/demo with the standard layout
higgs projects                   # lists the Projects under the projects root
higgs models list --mode i2v     # Models with their Family, Modes, quality tiers and "from" price
higgs models show kling-3.0      # every Endpoint of a Model: parameters, allowed values, prompt limits
cd ~/hf-projects/demo
higgs gen higgsfield-ai/soul/v2/standard -p prompt="Editorial portrait" -p batch_size=4 \
  --no-wait                      # validates, submits a Request; its record lands in .hf/requests/<request_id>.json
```

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

For now `higgs gen` also requires `--no-wait` (waiting for and downloading the result is not built yet).

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

## Development

Development, type checking and tests run in the same container:

```sh
scripts/dev test               # pytest (extra args are passed through, e.g. scripts/dev test -k version)
scripts/dev typecheck          # mypy --strict
scripts/dev shell              # a shell in the dev container
scripts/dev catalog            # rebuild src/hfcore/catalog/catalog.json from the snapshots in catalog/
```

To refresh the model catalog, replace the snapshots and rebuild:

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
