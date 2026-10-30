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
cd ~/hf-projects/demo
higgs gen higgsfield-ai/soul/v2/standard -p prompt="Editorial portrait" -p batch_size=2 \
  --no-validate --no-wait        # submits a Request; its record lands in .hf/requests/<request_id>.json
```

The key can also come from `HF_KEY="id:secret"` or `HF_API_KEY` + `HF_API_SECRET`, which take precedence
over the saved file. Project-scoped commands find the Project by walking up from the current directory to
the nearest `project.toml`, like git; `--project <name>` picks another one. `-p` values are parsed as JSON
when they are valid JSON (`5`, `0.5`, `true`, `null`, `[...]`, `{...}`) and are strings otherwise
(`16:9`); quote a value to force a string (`-p 'seed="42"'`). Every command takes `--json`.

For now `higgs gen` requires `--no-validate` (the model catalog is not built yet) and `--no-wait`
(waiting for and downloading the result is not built yet).

## Development

Development, type checking and tests run in the same container:

```sh
scripts/dev test               # pytest (extra args are passed through, e.g. scripts/dev test -k version)
scripts/dev typecheck          # mypy --strict
scripts/dev shell              # a shell in the dev container
```

The repo is mounted over `/app`, where the package is installed in editable mode, so source changes
are picked up without a rebuild; changes to `pyproject.toml` or the `Dockerfile` rebuild the image.

Tests follow the seams agreed in the spec (#4): they invoke the `higgs` command in-process with
`CliRunner`, replace HTTP at the transport level with `respx`, and use a temporary projects root and
config dir (`HIGGS_PROJECTS_ROOT`, `HIGGS_CONFIG_DIR`) per test. Internal functions are never mocked.
