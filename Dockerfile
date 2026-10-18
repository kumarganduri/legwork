# For MCP directories (such as Glama) that build a server and list its tools.
#
# Legwork's sandbox (bubblewrap) can't run in an unprivileged container, and
# Legwork never runs anything without it. So this image starts the hub with
# --inspect-only: it answers initialize and tools/list, and refuses every
# install. To use Legwork, install it on your own machine (see README.md).
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY legwork ./legwork
RUN pip install --no-cache-dir . && useradd --create-home legwork
USER legwork
ENTRYPOINT ["legwork", "hub", "--inspect-only"]
