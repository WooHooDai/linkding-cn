#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

version=$(<version.txt)
tag="v$version"

[[ $(git branch --show-current) == main ]] || { echo "Release from main." >&2; exit 1; }
[[ -z $(git status --porcelain) ]] || { echo "Commit release changes first." >&2; exit 1; }
if git show-ref --verify --quiet "refs/tags/$tag"; then
    [[ $(git rev-list -n 1 "$tag") == $(git rev-parse HEAD) ]] || {
        echo "$tag already identifies another commit (possibly an upstream tag). Choose an unused version; existing tags are not overwritten." >&2
        exit 1
    }
else
    git tag -a "$tag" -m "Release $tag"
fi
git push --atomic origin HEAD:main "refs/tags/$tag"

echo "Source released. Run the Docker images workflow to validate and publish images."
