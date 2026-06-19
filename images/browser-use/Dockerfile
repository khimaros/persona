# khimaros/browser-use: a browser and the tooling to drive it, as an image in its own right.
#
# it is a BASE, not an add-on, and that is the whole design. chrome is ~1.4GB of apt packages that
# change about never, but a container layer is invalidated by anything beneath it -- so installed
# ON TOP of an image that is rebuilt every release (which is what persona used to do on top of
# hmux) those packages were remade every time, got fresh digests, and were re-uploaded on every
# publish. measured on persona: 1575MB of a 2747MB image, 1432MB of it chrome. underneath the
# things that change, they keep their digests and upload once.
#
# it is also the only direction that composes. an OCI image is a linear stack with one parent, so
# there is no real "mixin" -- the closest thing is `COPY --from=<image>`, and that only works for
# payloads that are SELF-CONTAINED. hmux's is (/opt/hmux + /opt/mise/installs + an installer it
# ships itself), so hmux can be mixed INTO this. chrome's is not: apt spreads it over
# /usr/lib/x86_64-linux-gnu, /usr/share/fonts and /opt/google/chrome and leaves dpkg, ldconfig and
# font-cache state behind, so copying it out of here would half-work and then break on an upgrade.
#
# self-contained: everything it needs is in this directory.
#   docker build -t khimaros/browser-use:latest images/browser-use

ARG DEBIAN_TAG=forky-slim
FROM debian:${DEBIAN_TAG}

LABEL org.opencontainers.image.title="browser-use" \
      org.opencontainers.image.description="google-chrome + the browser-use CLI, as a base image for agents that drive a browser" \
      org.opencontainers.image.source="https://github.com/khimaros/persona" \
      org.opencontainers.image.licenses="GPL-3.0"

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/usr/local/bin:/usr/bin:/bin

# the general runtime set for a container that runs tooling: certs for outbound https, git, tini
# as a well-behaved PID 1, and the shared libs a node binary links. an image mixed in on top (see
# the header) needs these too, and they belong down here with the other stable packages rather
# than in a layer above the payload, where they would be remade on every downstream build.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git tini libatomic1 libstdc++6 libgcc-s1 \
 && rm -rf /var/lib/apt/lists/*

# real google-chrome, not chromium: browser-use drives `google-chrome` over CDP and chromium is
# not a drop-in. installed WITH recommends so its runtime libs and fonts come along. python3 is
# both browser-use's interpreter and (downstream) persona's hook runtime.
ARG CHROME_DEB_URL=https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
RUN apt-get update && apt-get install -y --no-install-recommends python3 curl \
 && curl -fsSL -o /tmp/chrome.deb "$CHROME_DEB_URL" \
 && apt-get install -y /tmp/chrome.deb \
 && rm -f /tmp/chrome.deb && rm -rf /var/lib/apt/lists/*

# the browser-use CLI, pinned to the upstream COMMIT the skill was written against: `connect`
# landed on main after the 0.12.6 tag (published 0.12.6 has it only as a --connect flag).
# HOME is a volume downstream, so uv goes to /usr/local/bin and the tool venv to /opt/uv against
# the SYSTEM python3 -- nothing under HOME, which a runtime volume would mask.
ARG BROWSER_USE_REF=8fc4f34a
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
 && UV_TOOL_DIR=/opt/uv UV_TOOL_BIN_DIR=/usr/local/bin UV_PYTHON_DOWNLOADS=never \
      uv tool install --python /usr/bin/python3 \
      "browser-use[cli] @ git+https://github.com/browser-use/browser-use@${BROWSER_USE_REF}"

# the driving scripts (browser-head: start/stop/status a chrome the CLI is connected to). they
# belong to THIS image -- an image that ships a browser and no way to drive it is half an
# interface -- so the source lives beside this Dockerfile rather than in a consumer's tree.
#
# COPIED LAST, after chrome, and that ordering is the point: a layer is invalidated by everything
# beneath it, so a script edit above the packages rebuilds a few kB, while the same edit below
# them would rebuild and re-upload 1.4GB. the SKILL that documents these commands stays in
# persona; only the executables are here.
COPY scripts/ /usr/local/bin/
RUN chmod +x /usr/local/bin/browser-head \
 && google-chrome --version \
 && browser-use --help >/dev/null \
 && python3 --version
