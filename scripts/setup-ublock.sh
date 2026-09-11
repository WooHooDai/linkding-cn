#!/bin/sh
# Also used by the Docker builds to set up uBlock Origin Lite, see docker/*.Dockerfile
set -eu

# Select the latest stable release that provides a Chromium extension.
# Prefer a caller-provided token to avoid the unauthenticated GitHub API limit.
if [ -z "${GITHUB_TOKEN:-}" ] && [ -n "${GH_TOKEN:-}" ]; then
  GITHUB_TOKEN="$GH_TOKEN"
fi
if [ -z "${GITHUB_TOKEN:-}" ] && [ -r /run/secrets/github_token ]; then
  GITHUB_TOKEN=$(cat /run/secrets/github_token 2>/dev/null || true)
fi
api_url="https://api.github.com/repos/uBlockOrigin/uBOL-home/releases?per_page=20"
if [ -n "${GITHUB_TOKEN:-}" ]; then
  json=$(curl --retry 3 -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" "$api_url" 2>/dev/null || true)
else
  json=$(curl --retry 3 -fsSL "$api_url" 2>/dev/null || true)
fi
DOWNLOAD_URL=""
if [ -n "$json" ]; then
  DOWNLOAD_URL=$(printf '%s' "$json" | jq -r 'first(.[] | select(.prerelease == false) | .assets[] | select(.name | endswith(".chromium.zip")) | .browser_download_url) // empty')
fi
if [ -z "$DOWNLOAD_URL" ]; then
  echo "No stable uBlock Origin Lite Chromium release found" >&2
  exit 1
fi
archive=$(mktemp)
trap 'rm -f "$archive"' EXIT

echo "Downloading $DOWNLOAD_URL"
curl --retry 3 -fsSL -o "$archive" "$DOWNLOAD_URL"
unzip -tq "$archive"
rm -rf uBOLite.chromium.mv3
unzip -q "$archive" -d uBOLite.chromium.mv3

# Enable annoyances rulesets in manifest.json
jq '.declarative_net_request.rule_resources |= map(if .id == "annoyances-overlays" or .id == "annoyances-cookies" or .id == "annoyances-social" or .id == "annoyances-widgets" or .id == "annoyances-others" then .enabled = true else . end)' uBOLite.chromium.mv3/manifest.json > temp.json
mv temp.json uBOLite.chromium.mv3/manifest.json

mkdir -p chromium-profile
