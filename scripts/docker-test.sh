#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
python_image=python:3.13-slim
node_image=node:22-bookworm-slim
uv_cache=bolivia-fire-uv-cache
npm_cache=bolivia-fire-npm-cache

docker_cmd() {
    MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' docker "$@"
}

check_space() {
    free_kib=$(df -Pk "$repo_root" | awk 'END {print $4}')
    if ! awk -v free="$free_kib" 'BEGIN {exit !(free >= 57671680)}'; then
        printf 'Need at least 55 GiB free on the repository drive; found %s KiB.\n' "$free_kib" >&2
        exit 1
    fi
    printf 'Repository drive free: %s KiB\n' "$free_kib"
}

run_stage() {
    stage=$1
    shift
    started=$(date +%s)
    printf '\n%s stage started\n' "$stage"
    if "$@"; then
        status=0
    else
        status=$?
    fi
    elapsed=$(($(date +%s) - started))
    printf '%s stage: exit=%s wall_seconds=%s\n' "$stage" "$status" "$elapsed"
    return "$status"
}

check_space
docker_cmd info --format 'Docker server: {{.ServerVersion}}' >/dev/null
for image in "$python_image" "$node_image"; do
    docker_cmd image inspect "$image" --format 'Image: {{index .RepoTags 0}} {{.Id}}'
done

temp_root=$(CDPATH= cd -- "${TMPDIR:-/tmp}" && pwd -P)
scratch=$(mktemp -d "$temp_root/bolivia-fire-docker-test.XXXXXX")
cleanup() {
    case "$scratch" in
        "$temp_root"/bolivia-fire-docker-test.*) rm -rf -- "$scratch" ;;
    esac
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$scratch/src"

# Sanitize before mounting: a direct repository mount would expose the host secret.
tar -C "$repo_root" -cf "$scratch/source.tar" \
    --exclude='.env' --exclude='.env.*' \
    --exclude='./.git' --exclude='./.venv' \
    --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.ruff_cache' \
    --exclude='.mypy_cache' --exclude='.hypothesis' \
    --exclude='./datasets/raw' --exclude='./datasets/interim' \
    --exclude='./datasets/processed' --exclude='./datasets/.tmp' \
    --exclude='./datasets/.quarantine' --exclude='./artifacts/models' \
    --exclude='./artifacts/predictions' --exclude='./artifacts/.cache' \
    --exclude='./artifacts/serving' --exclude='./frontend/node_modules' \
    --exclude='./frontend/dist' --exclude='./frontend/test-results' \
    --exclude='./frontend/playwright-report' --exclude='./data' .
tar -C "$scratch/src" -xf "$scratch/source.tar"
rm -- "$scratch/source.tar"
if [ -n "$(find "$scratch/src" -name .env -print)" ]; then
    printf 'Refusing to mount a snapshot containing .env.\n' >&2
    exit 1
fi
printf 'Sanitized source snapshot: '
du -sk "$scratch/src"
source_mount=$scratch/src
if command -v cygpath >/dev/null 2>&1; then
    source_mount=$(cygpath -m "$source_mount")
fi

docker_cmd volume create "$uv_cache" >/dev/null
docker_cmd volume create "$npm_cache" >/dev/null
printf 'Cache volumes: %s %s\n' "$uv_cache" "$npm_cache"

total_started=$(date +%s)
python_status=0
run_stage Python docker_cmd run --rm --pull never -i \
    --memory=3g --memory-swap=3g --cpus=2 --pids-limit=512 \
    --mount "type=bind,source=$source_mount,target=/src,readonly" \
    --mount "type=volume,source=$uv_cache,target=/cache/uv" \
    --env UV_CACHE_DIR=/cache/uv --env UV_LINK_MODE=copy \
    --env UV_PYTHON_DOWNLOADS=never --env OMP_NUM_THREADS=2 \
    --env OPENBLAS_NUM_THREADS=2 --env MKL_NUM_THREADS=2 \
    --workdir /work "$python_image" sh -eu -s <<'PYTHON' || python_status=$?
cp -R /src/. /work/
printf 'Python container workdir listing:\n'
ls -la /work
test -z "$(find /src /work -name .env -print)"
printf 'Verified: no .env in /src or /work.\n'
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git libexpat1 libgomp1
rm -rf /var/lib/apt/lists/*
python -m pip install --no-cache-dir uv
uv sync --all-extras --frozen
uv run ruff check src tests backend
uv run pytest -q -m "not live and not local_data"
uv run pytest -q -m "not live and not local_data" backend/tests
PYTHON
check_space

node_status=0
run_stage Frontend docker_cmd run --rm --pull never -i \
    --memory=3g --memory-swap=3g --cpus=2 --pids-limit=512 \
    --mount "type=bind,source=$source_mount,target=/src,readonly" \
    --mount "type=volume,source=$npm_cache,target=/cache/npm" \
    --env npm_config_cache=/cache/npm --env CI=true \
    --workdir /work "$node_image" sh -eu -s <<'NODE' || node_status=$?
cp -R /src/. /work/
printf 'Frontend container workdir listing:\n'
ls -la /work
test -z "$(find /src /work -name .env -print)"
printf 'Verified: no .env in /src or /work.\n'
cd frontend
npm ci
npm run check
npm run test
npm run build
NODE
check_space
printf '\nContainerized tests: python_exit=%s frontend_exit=%s wall_seconds=%s\n' \
    "$python_status" "$node_status" "$(($(date +%s) - total_started))"
if [ "$python_status" -ne 0 ] || [ "$node_status" -ne 0 ]; then
    exit 1
fi
