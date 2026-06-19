#!/bin/bash
# runtime setup for the persona container, run once per start before `hmux up`. all steps are
# idempotent: seed the workspace, place the provider config, and link skills. persona has no pi
# extensions anymore -- the hcp/bridge/permission faces are all hmux client faces (see hmux/config.toml).
set -euo pipefail

# the base image does not expose the pi cli on PATH (it lives under the mise install tree); put it
# there so the hmux pi backend can launch it.
pi_bin="$(dirname "$(ls /opt/mise/installs/npm-earendil-works-pi-coding-agent/*/bin/pi 2>/dev/null | head -1)")"
export PATH="${pi_bin:-}:$PATH"

# seed the workspace into /work on first boot only; afterwards it is the mutable,
# git-versioned workspace persona self-edits.
if [ ! -e /work/hooks/persona.py ]; then
  cp -a /opt/persona/workspace-seed/. /work/
fi
[ -d /work/.git ] || (cd /work && git init -q)

# pi agent config mounted read-only at /config, copied into /data so the pi backend
# (HOME=/data) picks up providers, ENABLED models + defaults (settings.json), and auth.
# /data is a volume, so create the config dir first: a fresh volume has no .pi/agent yet.
mkdir -p /data/.pi/agent
for f in models.json auth.json; do
  [ -f "/config/$f" ] && cp "/config/$f" "/data/.pi/agent/$f"
done

# settings.json is filtered to an explicit key allowlist instead of copied whole: the dev's
# host config carries keys the container must not inherit -- `packages` (pi extensions, which
# persona runs as hmux faces instead, and whose install triggers a live npm download on the
# first session) and `theme`/`themes` (a local theme dir the image does not ship). every key
# below is preserved; anything not listed is dropped, so a new host key never silently leaks in.
if [ -f /config/settings.json ]; then
  SETTINGS_KEYS="lastChangelogVersion defaultProvider defaultModel hideThinkingBlock defaultThinkingLevel collapseChangelog quietStartup enableInstallTelemetry terminal transport doubleEscapeAction treeFilterMode enabledModels" \
    node -e '
      const fs = require("fs");
      const allow = new Set(process.env.SETTINGS_KEYS.split(" "));
      const src = JSON.parse(fs.readFileSync("/config/settings.json", "utf8"));
      const out = {};
      for (const k of Object.keys(src)) if (allow.has(k)) out[k] = src[k];
      fs.writeFileSync("/data/.pi/agent/settings.json", JSON.stringify(out, null, 2) + "\n");
    '
fi

# install skills into the per-backend discovery dirs (HOME=/data is a volume, so link at
# runtime). common skills work with EITHER backend -> linked into both the pi and opencode
# skill dirs; opencode-specific skills go to opencode's standard dir only.
mkdir -p /data/.pi/agent/skills /data/.config/opencode/skills
for d in /opt/persona/skills/*/; do
  d="${d%/}"; name="$(basename "$d")"
  ln -sfn "$d" "/data/.pi/agent/skills/$name"
  ln -sfn "$d" "/data/.config/opencode/skills/$name"
done
for d in /opt/persona/opencode-skills/*/; do
  d="${d%/}"; name="$(basename "$d")"
  ln -sfn "$d" "/data/.config/opencode/skills/$name"
done

# no pi extensions to register: the hcp, bridge, and permission faces are all hmux client faces now
# (see hmux/config.toml), composed over the hub rather than loaded into pi.

# the setup above needs root (seed /work, write /data, read the /config mounts); hand off to the base
# image's hmux-drop, which chowns the volumes to `hmux` and drops privileges before exec'ing `hmux up
# persona`. so the whole agent -- pi, faces, chrome, bash -- runs unprivileged (uid 1000). see the
# hmux Dockerfile (33e) and browser-head, which no longer needs --no-sandbox as a result.
exec /usr/local/bin/hmux-drop "$@"   # drop to hmux, then -> hmux up persona
