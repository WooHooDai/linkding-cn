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

# Update package.json
sed -i '' "s/\"version\": \".*\"/\"version\": \"$version\"/" package.json

# Update package-lock.json (root entry + top-level packages entry)
sed -i '' "1,5s/\"version\": \".*\"/\"version\": \"$version\"/" package-lock.json
sed -i '' "/\"\": {/{n;s/\"version\": \".*\"/\"version\": \"$version\"/;}" package-lock.json

echo "Version updated to $version in:"
echo "  - version.txt"
echo "  - pyproject.toml"
echo "  - package.json"
echo "  - package-lock.json"
