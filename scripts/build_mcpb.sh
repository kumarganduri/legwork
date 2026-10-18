#!/bin/sh
# Builds the Claude Desktop extension, dist/legwork.mcpb, for a release
# that's already on PyPI: the bundle installs legwork-mcp at this version,
# pinned by hash in the uv.lock made here (it can't be committed with the
# version bump: the release isn't on PyPI yet then).
set -eu
cd "$(dirname "$0")/.."
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)
curl -sf "https://pypi.org/pypi/legwork-mcp/$version/json" >/dev/null \
  || { echo "legwork-mcp $version isn't on PyPI yet: publish the release first" >&2; exit 1; }
rm -rf mcpb/.venv mcpb/uv.lock
uv lock --directory mcpb -q
grep -q "^version = \"$version\"" mcpb/uv.lock
mkdir -p dist
npx -y @anthropic-ai/mcpb@2.1.2 pack mcpb dist/legwork.mcpb
