# Contributing

## Branches

- **`master`** holds released code only. Every change that reaches it is a release with a
  version tag.
- **`develop`** is where work comes together. It is the default branch, so pull requests
  target it unless told otherwise.

Both branches are protected: no direct pushes, no force-pushes, no deletion. Changes reach
them only through a pull request with a green CI run.

## Day-to-day work

1. Branch off `develop`. Name the branch after its issue: `fix/44-relative-paths`,
   `feat/24-something`, `chore/ci`, `docs/…`.
2. Commit and push the branch, then open a pull request into `develop`. Put
   `Closes #<issue>` in the description.
3. Merge it once CI is green.

Pull requests are merged with a **merge commit**; squash and rebase merges are switched
off. History that has been pushed is never rewritten: fix things with a new commit.

## Releases

Versions follow [Semantic Versioning](https://semver.org): `0.1.1` for fixes, `0.2.0` for
new features (while the major version is 0, a minor bump may also break things).

1. Open a release pull request **`develop` → `master`**, titled `Release vX.Y.Z`. It bumps
   `version` in `pyproject.toml` and lists the changes since the last release.
2. The release pull request is reviewed together and merged by the maintainer.
3. Tag the merge commit on `master` and publish a GitHub Release:

   ```sh
   git tag -a vX.Y.Z -m "higgsflow X.Y.Z" origin/master
   git push origin vX.Y.Z
   gh release create vX.Y.Z --verify-tag --generate-notes
   ```

## Hotfixes

For an urgent fix to a release: branch off `master` (`hotfix/…`), open a pull request into
`master` that also bumps the patch version, release it as above, then open a pull request
`master` → `develop` so the fix is not lost.

## Checks

CI (`.github/workflows/ci.yml`) builds the Docker image and runs the tests and mypy inside
it on every pull request. Run the same checks locally before pushing:

```sh
scripts/dev test
scripts/dev typecheck
```
