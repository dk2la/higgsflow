# Shared by bin/higgs and scripts/dev. Sourced, not executed.
#
# ADR 0003: always Docker Desktop via its explicit path and context; never a bare
# `docker`, because Rancher Desktop's ~/.rd/bin/docker comes first in PATH.

HIGGS_DOCKER=(/usr/local/bin/docker --context desktop-linux)
HIGGS_IMAGE_REPO="higgsflow"

# Directory of the repository, resolved through symlinks (~/.local/bin/higgs -> repo/bin/higgs).
higgs_repo_dir() {
  local source="${BASH_SOURCE[0]}"
  while [ -L "$source" ]; do
    local dir
    dir="$(cd -P "$(dirname "$source")" && pwd)"
    source="$(readlink "$source")"
    [[ "$source" != /* ]] && source="$dir/$source"
  done
  cd -P "$(dirname "$source")/.." && pwd
}

# Content hash of everything that goes into the image. The tag is derived from it,
# so a changed tree yields a new tag (and a rebuild) and an unchanged tree reuses the image.
higgs_source_hash() {
  local repo="$1"
  (
    cd "$repo" &&
      find Dockerfile pyproject.toml README.md src catalog -type f \
        -not -path '*/__pycache__/*' -not -name '*.pyc' 2>/dev/null |
      LC_ALL=C sort |
      xargs shasum -a 256 |
      shasum -a 256 |
      cut -c1-16
  )
}

# Prints the image reference, building it first if this source tree has not been built yet.
higgs_ensure_image() {
  local repo hash image
  repo="$(higgs_repo_dir)"
  hash="$(higgs_source_hash "$repo")"
  image="$HIGGS_IMAGE_REPO:$hash"
  if ! "${HIGGS_DOCKER[@]}" image inspect "$image" >/dev/null 2>&1; then
    echo "higgs: building image $image ..." >&2
    "${HIGGS_DOCKER[@]}" build --quiet --tag "$image" --tag "$HIGGS_IMAGE_REPO:latest" "$repo" >/dev/null || return 1
    # Drop the tags of earlier source trees so old images do not pile up.
    "${HIGGS_DOCKER[@]}" image ls "$HIGGS_IMAGE_REPO" --format '{{.Repository}}:{{.Tag}}' |
      grep -v -e ":latest\$" -e ":$hash\$" |
      xargs "${HIGGS_DOCKER[@]}" image rm >/dev/null 2>&1 || true
  fi
  echo "$image"
}
