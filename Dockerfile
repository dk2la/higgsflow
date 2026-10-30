FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Editable install: the dev script mounts the repo over /app and runs the
# tests against the live source without rebuilding.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e ".[dev]"

COPY . .

ENTRYPOINT ["higgs"]
