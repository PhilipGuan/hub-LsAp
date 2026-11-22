#!/usr/bin/env bash
set -e

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

exec docker build \
  -f "$project_root/docker/Dockerfile" \
  -t emotion-analysis-openaiagents:local \
  "$project_root"
