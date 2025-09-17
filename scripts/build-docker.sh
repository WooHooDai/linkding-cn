#!/usr/bin/env bash

version=$(<version.txt)

# Plus image with support for single-file snapshots
# Needs checking if this works with ARMv7, excluded for now
docker buildx build --target linkding-plus --platform linux/amd64,linux/arm64 \
  -f docker/default.Dockerfile \
  -t woohoodai/linkding-cn:latest \
  -t woohoodai/linkding-cn:v$version \
  --push .
