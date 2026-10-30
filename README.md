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
cd ~/hf-projects/demo
higgs gen higgsfield-ai/soul/v2/standard -p prompt="Editorial portrait" -p batch_size=2 \
  --no-validate                  # submits a Request, waits for it and downloads the Generations
higgs gen bytedance/seedance-2.0/text-to-video -p prompt="Waves at dusk" --name sunset-beach \
  --no-validate --no-wait        # only submits; the record lands in .hf/requests/<request_id>.json
higgs pick generations/2026-10-30/<file>.png
                                 # copies the Generation, its .json and Artifacts into final/
```

Without `--no-wait`, `higgs gen` polls the Request (2 s backing off to 10 s, waiting out a full queue and
retrying transient errors) and downloads every Generation and Artifact into `generations/YYYY-MM-DD/` as
`YYYYMMDD-HHMM_<model-slug>_<mode>_<slug>_vNN[-N].<ext>`, each with a `<file>.json` describing how it was
made. `<slug>` is `--name` or the first words of the prompt; `vNN` counts the Project's Requests with that
slug. The model slug and Mode come from the catalog, with the quality tier when the Endpoint has one
(`kling-video/v3.0/pro/image-to-video` → `kling-3-0-pro_i2v`); for an Endpoint the catalog lacks they come
from the Endpoint id (see DESIGN.md §5). A failed, nsfw
or canceled Request ends with an error and downloads nothing.

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

For now `higgs gen` requires `--no-validate` (validation against the catalog is not built yet).

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
