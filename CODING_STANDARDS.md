# Coding standards

The rules a reviewer applies to a diff. Mechanical rules live in the checks, so a green CI
run already covers them:

- `scripts/dev lint` (ruff), with its rules in `pyproject.toml` → `[tool.ruff]`.
- `scripts/dev typecheck` (mypy, strict).
- `tests/test_architecture.py`: `hfcore` loads no Typer, Rich or Click, and reads the time
  only through `hfcore.clock`.

What follows are the judgement calls no check can make. Each comes from findings that kept
coming back in earlier reviews.

## Sources of truth

- **`DESIGN.md`** is the behaviour spec: commands, flags, storage layout, error codes and
  hints, the generation lifecycle.
- **`GLOSSARY.md`** defines the domain terms and the words to avoid.
- **`docs/adr/`** records decisions. A change that goes against an ADR adds a new ADR that
  supersedes it.

## Docs move with the behaviour

When a diff changes what the code does, every description of that behaviour changes in the
same pull request: the `DESIGN.md` section, module and function docstrings, comments next to
the code, `README.md`, and `--help` text. Review a behaviour change by searching for the old
wording. A docstring or comment that still describes the old behaviour is a finding.

## Glossary terms

Code names, user-facing messages and docs use `GLOSSARY.md` terms in their defined sense and
spelling (Project, Request, Generation, Input, Reference, …). A term used for something
outside its definition is **drift**: for example, calling a Soul ID training image an
"Input", although an Input belongs to a Request. Fix it with the right term or by updating
the glossary in the same pull request.

## Layers

`hfcore` is the core that the stage 2 web server will also call (`DESIGN.md` §3). It owns
error codes, messages and hints (`HiggsError`), and reports through return values, progress
callbacks and `HiggsWarning`. `hfcli` owns rendering, prompts, exit codes and output
formats, and stays thin: logic that a web server would also need goes down into `hfcore`.

## Complete fixes

A fix covers every path its issue names and every path that shares the behaviour: text and
`--json` output, each command that takes the same flag, single Requests and `--count`
batches. When the issue lists cases, the pull request description says where each one is
handled. When some are left out on purpose, it says why.

## Tests

Tests follow `DESIGN.md` §10, using the fixtures in `tests/conftest.py`. A bug fix comes with
a test that is **red** without the fix.
