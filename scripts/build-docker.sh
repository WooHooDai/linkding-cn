#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
    cat <<'EOF'
Usage: scripts/build-docker.sh [options] [-- additional buildx options]
  --base                 Build without Chromium (reading support is retained)
  --alpine               Alpine base variant; requires --base
  --platform PLATFORMS   Default: native for --load, amd64+arm64 for --push
  --load                 Load locally (default; does not publish)
  --push                 Publish after the release checks have passed
  --repository NAME      Repository for generated tags; repeat for multiple registries
  --tag IMAGE            Override generated tags; repeat for multiple tags
  --print-tags           Print tags without building (used to publish tested artifacts)
  --oci PATH             Also export compressed OCI layers for size checks
  --no-mirror            Use the official Debian/Alpine package repositories
EOF
}
base=false
alpine=false
mirror=true
mode=load
print_tags=false
platforms=""
oci=""
repositories=()
tags=()
extra=()
while (($#)); do
    case "$1" in
        --base) base=true; shift ;;
        --alpine) alpine=true; shift ;;
        --no-mirror) mirror=false; shift ;;
        --load|--push) mode=${1#--}; shift ;;
        --print-tags) print_tags=true; shift ;;
        --platform) platforms=${2:?Missing platform}; shift 2 ;;
        --repository) repositories+=("${2:?Missing repository}"); shift 2 ;;
        --tag) tags+=("${2:?Missing image tag}"); shift 2 ;;
        --oci) oci=${2:?Missing OCI path}; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        --) shift; extra+=("$@"); break ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done
if $alpine && ! $base; then
    echo "Alpine Plus is unsupported by Python Playwright. Use --base --alpine, or Debian Plus." >&2
    exit 2
fi
if [[ -z "$platforms" ]]; then
    if [[ "$mode" == push ]]; then
        platforms=linux/amd64,linux/arm64
    else
        platforms=linux/$(docker version --format '{{.Server.Arch}}')
    fi
fi
if [[ "$mode" == load && "$platforms" == *,* ]]; then
    echo "--load requires one platform; use --push for a multi-platform release." >&2
    exit 2
fi
IFS=, read -r -a platform_list <<< "$platforms"
for platform in "${platform_list[@]}"; do
    case "$platform" in
        linux/amd64|linux/arm64) ;;
        *) echo "Unsupported platform: $platform" >&2; exit 2 ;;
    esac
done

version=$(<version.txt)
revision=$(git rev-parse HEAD)
git diff --quiet HEAD -- || revision="$revision-dirty"
file=docker/default.Dockerfile
target=linkding-plus
suffix=""
args=()
# Refresh the extension release while retaining cached build tools and packages.
if ! $base; then args+=(--no-cache-filter ublock-build); fi
if $base; then
    target=linkding
    suffix=-base
fi
if $alpine; then
    file=docker/alpine.Dockerfile
    suffix=-base-alpine
    $mirror && args+=(--build-arg APK_MIRROR=mirrors.tuna.tsinghua.edu.cn)
else
    $mirror && args+=(--build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn)
fi
if (("${#tags[@]}" == 0)); then
    if (("${#repositories[@]}" == 0)); then repositories=(woohoodai/linkding-cn); fi
    for repository in "${repositories[@]}"; do
        tags+=("$repository:latest$suffix" "$repository:v$version$suffix")
    done
fi
if $print_tags; then printf '%s\n' "${tags[@]}"; exit 0; fi
for tag in "${tags[@]}"; do args+=(-t "$tag"); done
if [[ -n "$oci" ]]; then
    args+=(--output "type=oci,dest=$oci,compression=gzip,compression-level=6")
fi
# Authenticate the GitHub API during the build (e.g. uBlock release lookup).
# Use the caller's GitHub token when available; CI provides ${{ github.token }}.
if [[ -z "${GITHUB_TOKEN:-}" && -n "${GH_TOKEN:-}" ]]; then
    GITHUB_TOKEN="$GH_TOKEN"
fi
if [[ -z "${GITHUB_TOKEN:-}" ]] && command -v gh >/dev/null 2>&1; then
    GITHUB_TOKEN=$(gh auth token 2>/dev/null || true)
fi
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    secret_file=$(mktemp)
    trap 'rm -f "$secret_file"' EXIT
    printf '%s' "$GITHUB_TOKEN" > "$secret_file"
    chmod 600 "$secret_file"
    args+=(--secret "id=github_token,src=$secret_file")
fi
docker buildx build --target "$target" --platform "$platforms" -f "$file" \
    --build-arg VERSION="$version" --build-arg REVISION="$revision" \
    "${args[@]}" --"$mode" ${extra[@]+"${extra[@]}"} .
