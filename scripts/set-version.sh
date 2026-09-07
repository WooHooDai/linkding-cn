#!/usr/bin/env bash
set -euo pipefail

if [ -z "${1:-}" ]; then
  echo "Usage: $0 <version>"
  echo "Example: $0 1.0.5"
  exit 1
fi

version=$1

# Update version.txt (source of truth for release scripts)
echo "$version" > version.txt

# Update pyproject.toml
sed -i '' "s/^version = \".*\"/version = \"$version\"/" pyproject.toml

# Regenerate uv.lock so the project version tracked by uv stays in sync.
# Without --upgrade, uv keeps the existing dependency resolution intact.
uv lock

# Update package.json and project versions in package-lock.json.
npm version "$version" \
  --no-git-tag-version \
  --allow-same-version \
  --ignore-scripts

echo "Version updated to $version in:"
echo "  - version.txt"
echo "  - pyproject.toml"
echo "  - uv.lock"
echo "  - package.json"
echo "  - package-lock.json"
