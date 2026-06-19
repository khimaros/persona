# persona: a thin image on top of hmux. it adds the skills, the workspace seed and the `up`
# profile; hmux itself (hub + pi/opencode backends + faces) and the browser runtime both come
# from the base image. this image just drives `hmux up persona`. see HMUX.md.
#
# BUILD THE BASE FIRST, or this image silently ships whatever hmux payload was lying around:
#
#     cd ../hmux && make image
#
# nothing here can notice a stale base -- the copy below succeeds either way -- so a persona
# release carrying a month-old webui looks exactly like a fresh one. (this comment named a
# `make image-browser` target that no longer exists, and following it published twice from an
# old base before the difference was spotted in the image itself.)

# persona is TWO images mixed together: khimaros/browser-use (chrome, python3, the browser-use
# cli, browser-head) as the base, and khimaros/hmux's payload copied in on top.
#
# which one is the base is not a style choice. an OCI image has a single parent, so the closest
# thing to a mixin is `COPY --from`, and that only works for a payload that is SELF-CONTAINED.
# hmux's is -- two directories plus an installer it ships itself. chrome's is not: apt spreads it
# across /usr/lib, /usr/share/fonts and /opt/google/chrome and leaves dpkg/ldconfig/font-cache
# state behind, so copying it would half-work and break on the next chrome release.
#
# it also puts the layers in the right order. chrome is ~1.4GB that never changes; hmux's payload
# changes every release. with the browser on top (persona's old shape, FROM hmux) every release
# invalidated it, so 1.4GB got new digests and was re-uploaded EVERY publish -- measured at 1575MB
# of a 2747MB image. underneath, it keeps its digests and uploads once.
# the two halves, as ARGs so a build can name the EXACT image it means. a bare
# `khimaros/hmux:latest` is ambiguous the moment a `docker.io/khimaros/hmux:latest` of the same
# name also exists locally -- which it does after any publish -- and the resolver may pick the
# remote-tagged one. that is not a theoretical worry: it silently mixed in an older hmux whose
# payload predated hmux-install, and the build failed four steps later with "not found".
ARG BROWSER_USE_IMAGE=khimaros/browser-use:latest
ARG HMUX_IMAGE=khimaros/hmux:latest

FROM ${HMUX_IMAGE} AS hmux

FROM ${BROWSER_USE_IMAGE}

LABEL org.opencontainers.image.title="persona" \
      org.opencontainers.image.description="persona (Per): a self-modifying AI agent on the HCP hook contract, served by hmux" \
      org.opencontainers.image.source="https://github.com/khimaros/persona" \
      org.opencontainers.image.licenses="GPL-3.0"

# the hmux payload: the two self-contained trees, then hmux's OWN installer to wire them up
# (PATH symlinks, the uid-1000 hmux user, /work + /data, hmux-drop). persona deliberately does
# none of that itself -- a copy of hmux's runtime setup here would drift the first time hmux moved
# a path, and hmux runs this same script in its own image, so it cannot rot unnoticed.
COPY --from=hmux /opt/mise/installs /opt/mise/installs
COPY --from=hmux /opt/hmux          /opt/hmux
RUN /opt/hmux/hmux-install

# the base image's /usr/local/bin/npm is a mise-wrapper symlink that misresolves npm-cli.js
# when invoked by that path AND calls `mise reshim` (this image has no mise). pi's extension
# loader spawns `npm` when a session opens, so replace npm/npx with plain wrappers that run
# the real cli from the node install -- otherwise session creation crashes (502).
RUN NODE_BIN="$(dirname "$(readlink -f /usr/local/bin/node)")" \
 && printf '#!/bin/sh\nexec "%s/node" "%s/../lib/node_modules/npm/bin/npm-cli.js" "$@"\n' "$NODE_BIN" "$NODE_BIN" > /usr/local/bin/npm \
 && printf '#!/bin/sh\nexec "%s/node" "%s/../lib/node_modules/npm/bin/npx-cli.js" "$@"\n' "$NODE_BIN" "$NODE_BIN" > /usr/local/bin/npx \
 && chmod +x /usr/local/bin/npm /usr/local/bin/npx

# persona has NO pi extensions anymore -- all three are hmux client FACES now (see hmux/config.toml):
# the `hcp` face (prompt composition + persona's tools + heartbeat), the `bridge` face (matrix), and
# the `permission` face (an hmux-native allow/ask/deny gate). so there is nothing to install or
# register here; the model-facing behavior is composed by the faces over the hub.

# browser-use itself comes from the base (hmux `browser-base`). docker-compose.yml forwards the
# host X11 socket + $DISPLAY so `make browser-head` opens a headed window on the user's desktop
# (the container analog of the old ssh -X path); browser-head falls back to headless when no
# display is reachable.

# skills: common (both backends) + opencode-specific, baked read-only; the entrypoint links
# them into the per-backend skill dirs under /data (a volume) at runtime. everything here is
# runnable by the agent -- host-side operator tooling lives in scripts/, which never ships.
COPY skills /opt/persona/skills
COPY opencode/skills /opt/persona/opencode-skills
# browser-head is NOT installed here: it belongs to the browser-use base, which ships it on PATH
# already. persona keeps only the SKILL that documents it.

# bake the persona `up` profile so the image runs standalone; the compose mount at
# /config/config.toml overrides it.
COPY hmux/config.toml /config/config.toml

# the workspace seed (traits/hooks/prompts/config); the entrypoint copies it into the
# /work volume on first boot, after which /work is the mutable, git-versioned workspace.
COPY workspace /opt/persona/workspace-seed
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# no backend-specific working-dir env here: hmux derives it from the profile `cwd = "/work"`
# (hmux/config.toml) and sets PI_PROJECT_CWD on the pi backend + --cwd on the faces itself.

# these came from the hmux base when persona was FROM it; declared here now that hmux is copied in
# rather than inherited. HMUX_* env is NOT re-declared: it ships with the payload in
# /opt/hmux/hmux.env and hmux-drop sources it, so there is one copy and an operator's override
# still wins (see that file).
VOLUME ["/work", "/data"]
WORKDIR /work

# HOME is /data, the volume that holds credentials and sessions -- the pi backend reads
# ~/.pi/agent from it. it is set HERE and not defaulted in hmux.env like the HMUX_* vars, because
# a default (`: "${HOME:=/data}"`) can never fire: a container runtime always sets HOME, so it was
# silently staying /root and pi would have looked for its auth in the wrong place.
ENV HOME=/data
# hub, webui, omni, opencode face, openai face (published as needed).
EXPOSE 4280 4282 4284 4096 4286

# keep tini as PID1 (from the base), run persona's setup, then exec the CMD.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["hmux", "up", "persona"]
