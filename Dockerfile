FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, keyed only on pyproject.toml, so a source change does not
# reinstall them. Editable install: the dev script mounts the repo over /app and
# runs against the live source without rebuilding.
COPY pyproject.toml README.md ./
RUN mkdir -p src/hfcore src/hfcli \
    && touch src/hfcore/__init__.py src/hfcli/__init__.py \
    && pip install -e ".[dev]"

COPY . .

ENTRYPOINT ["higgs"]
