# dev/clean-room/Dockerfile: a stranger's machine for the clean-room check and the try-it shell.
#
# WHAT THIS IS
#   Python 3.9 (the plugin's declared floor), git and the Claude Code CLI at the version public CI
#   pins, and nothing else of the plugin's: no plugin, no settings, no login, no credentials. The
#   image holds run.sh so the check needs no host mount except the tree under test, read-only.
#   Built and run by the Makefile beside it: `make gate` runs the check, `make try` opens a shell.
#
# STAGES
#   cli      npm installs @anthropic-ai/claude-code; its postinstall puts the native binary in bin/.
#   runtime  python:3.9-slim plus git; the native binary is copied in, so node and npm do not ship.
#
# PINS
#   Both base images are pinned by multi-arch index digest (amd64 and arm64). To move a pin, read
#   the new digest with `docker buildx imagetools inspect <image>:<tag>` and edit the FROM line.
#   CLAUDE_CODE_VERSION must equal CLAUDE_CODE_VERSION in .github/workflows/tests.yml.
#
# BUILD ARGS
#   CLAUDE_CODE_VERSION  the CLI version to install (default 2.1.288)

ARG CLAUDE_CODE_VERSION=2.1.288

# node:22-bookworm-slim, resolved 2026-10-17
FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS cli
ARG CLAUDE_CODE_VERSION
ENV DISABLE_AUTOUPDATER=1
RUN npm install --global "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe --version

# python:3.9-slim-bookworm, resolved 2026-10-17
FROM python:3.9-slim-bookworm@sha256:a02e9c5406c416c504d6c9a1a306ff4080c3173f1008d192f953bd20382a2d5c AS runtime
ARG CLAUDE_CODE_VERSION
LABEL org.opencontainers.image.title="repacted clean room" \
      org.opencontainers.image.description="Python 3.9, git and the Claude Code CLI for the repacted clean-room check" \
      dev.repacted.claude-code-version="${CLAUDE_CODE_VERSION}"

# git for the scratch project and the hooks; perl-base (already in the image) gives run.sh its
# per-command timeout. No recommends, and the apt lists are dropped in the same layer.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

COPY --from=cli /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude
COPY run.sh /opt/cleanroom/run.sh

# A plain user with an empty home: the plugin is installed by hand (try) or by run.sh into a
# scratch HOME under /tmp (gate), never baked in.
RUN useradd --create-home --uid 10001 --shell /bin/bash stranger \
 && chmod 755 /opt/cleanroom/run.sh \
 && claude --version
USER stranger
ENV DISABLE_AUTOUPDATER=1 \
    PYTHONDONTWRITEBYTECODE=1

# The try-it shell starts in the user's home (useradd's default, read from the passwd entry).
CMD ["bash", "-c", "cd && exec bash"]
