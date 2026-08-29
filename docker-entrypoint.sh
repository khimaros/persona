#!/bin/bash
# runtime setup for the persona container, run once per start before `hmux up`. all steps are
# idempotent: seed the workspace, place the provider config, and link skills. persona has no pi
# extensions anymore -- hcp, bridge and permission are all hmux faces (see hmux/config.toml).
set -euo pipefail

# REFUSE TO BOOT rather than let the base image chown the deployment's own state away from it.
# docker-compose.yml bind-mounts ./work and ./data, and hmux-drop ends this script by running
# `chown -R hmux:hmux /work /data` whenever it starts as root. under rootless podman `hmux` (uid
# 1000 in here) is a SUBUID on the host, not the invoking user, so that chown rewrites the whole
# tree to a uid the host cannot read -- recoverable only with `podman unshare`, and silent enough
# that it reads as the agent having lost its memory rather than as a misconfiguration.
#
# the fix is a uid mapping, which cannot be applied from inside the container. so: say no, and say
# what to run. production is podman-compose 1.0.3, which ignores `user:` and `userns_mode:` in the
# compose file, which is exactly why the mapping is a run arg somebody can leave off.
#
# THREE SIGNALS, and all three are needed to avoid refusing something that is fine:
#   - we are root, so hmux-drop will take its chown branch at all;
#   - the userns is rootless (uid_map sends container 0 to a NON-ZERO host uid). rootful docker
#     maps 0 -> 0, where root IS root and the chown is the documented behaviour;
#   - the mount is a BIND, not a named volume. on a named volume the chown lands inside the
#     runtime's own storage and harms nobody, which is the README's standalone `podman run
#     -v persona-work:/work` recipe and must keep working.
guard_state_ownership() {
  [ "$(id -u)" = 0 ] || return 0
  local map_host_uid src
  map_host_uid="$(awk 'NR==1{print $2; exit}' /proc/self/uid_map 2>/dev/null || true)"
  [ -n "$map_host_uid" ] && [ "$map_host_uid" != 0 ] || return 0
  for d in /work /data; do
    # mountinfo field 4 is the source path within its filesystem, field 5 the mount point.
    src="$(awk -v d="$d" '$5==d {print $4; exit}' /proc/self/mountinfo 2>/dev/null || true)"
    case "$src" in
      ""|*/containers/storage/volumes/*|*/docker/volumes/*) continue ;;
    esac
    cat >&2 <<EOF
persona: refusing to start -- $d is a host directory ($src) and this container would chown it away
persona: from you. container uid 0 maps to host uid $map_host_uid here, so the unprivileged user
persona: the agent runs as is a subuid (not you), and the startup chown would make $d unreadable
persona: on the host until you run: podman unshare chown -R 0:0 <dir>
persona:
persona: start it with the uid mapping instead:
persona:   podman-compose --podman-run-args=--user=1000:1000 \\
persona:     --podman-run-args=--userns=keep-id:uid=1000,gid=1000 up
persona:
persona: (docker compose and newer podman compose read the same thing from the user: and
persona: userns_mode: keys already in docker-compose.yml; podman-compose 1.0.3 ignores them.)
EOF
    exit 1
  done
}
guard_state_ownership

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

# no pi extensions to register: hcp, bridge and permission are all hmux faces now
# (see hmux/config.toml), composed over the hub rather than loaded into pi.

# the container's own display, so chrome is HEADED and the canvas face has something to serve.
#
# THREE CASES, and conflating them is how this goes wrong:
#   a forwarded host $DISPLAY WINS and nothing is started -- the developer's desktop path, which
#     keeps working exactly as before, and the canvas then serves that display rather than an
#     empty one (the face takes the ambient $DISPLAY when it is not told otherwise). so ONE
#     setting decides which display chrome opens on AND which one is served.
#   our own display already up means a restart inside a live container: adopt it rather than
#     starting a second server on a number that is taken.
#   nothing at all: start one. this is the deployed case and the reason any of it exists.
#
# THE DISPLAY NUMBER IS A NAMESPACE SHARED WITH THE HOST, because docker-compose.yml bind-mounts
# /tmp/.X11-unix so a forwarded desktop works. that makes the conventional :99 a bad default --
# it is exactly what `xvfb-run` on a developer's machine takes first, and two servers on one
# number is a browser rendering into somebody else's session. hence :88, and PERSONA_DISPLAY to
# move it if that collides too.
# AN UNINTERPOLATED `${...}` IS NOT A VALUE, and the shell's `:-` default cannot save you from it:
# the variable is SET, just set to the literal text `${PERSONA_DISPLAY:-}`, so the default never
# fires and the garbage flows straight through to Xvfb and to the canvas face. It surfaced as
#
#   canvas: idle -- no source (connecting to x display ${PERSONA_DISPLAY:-})
#
# on a deployment whose compose file is consumed by something that does not interpolate (an older
# compose, podman-compose, or a file applied verbatim). Nothing else in the stack failed, so the
# only symptom was a face that idled -- and the value was RIGHT THERE in the message, which is the
# only reason it was findable at all.
#
# checked HERE because this is where every path to a display converges, and treated as UNSET
# rather than fatal: canvas is optional and killing the container over it would trade a dark
# screen for no agent at all. Loud, though -- a silent fallback is how it hid.
uninterpolated() {
  case "${1:-}" in *'${'*) return 0 ;; *) return 1 ;; esac
}
for var in PERSONA_DISPLAY PERSONA_SCREEN DISPLAY; do
  eval "value=\${$var:-}"
  if uninterpolated "$value"; then
    echo "persona: $var is the literal text '$value' -- your compose file was NOT interpolated." >&2
    echo "persona:   (compose substitutes \${VAR:-default} from the shell and .env; a tool that" >&2
    echo "persona:    applies the file verbatim does not.) treating $var as unset." >&2
    eval "$var=''"
  fi
done
unset value

PERSONA_DISPLAY="${PERSONA_DISPLAY:-:88}"
PERSONA_SCREEN="${PERSONA_SCREEN:-1280x800x24}"

# A DISPLAY THAT IS SET IS NOT A DISPLAY THAT ANSWERS, and trusting the variable is exactly how
# this broke in production: a developer's shell exports DISPLAY=:0, compose forwards it, and the
# entrypoint handed the whole stack a display nothing was listening on -- so chrome stayed headless
# and /canvas idled for the life of the container. LOOKING AT THE SOCKET DOES NOT HELP either: the
# bind mount carries the host's socket FILES but not its abstract sockets, so a desktop's X0 is
# right there, refuses every connection, and is indistinguishable from a working one.
# browser-head has always said this ("headed when $DISPLAY is set AND the X server is reachable");
# this asks the same question the same way, by connecting.
answers() {
  [ -n "${1:-}" ] || return 1
  command -v xdpyinfo >/dev/null 2>&1 || return 1
  xdpyinfo -display "$1" >/dev/null 2>&1
}

# UNPRIVILEGED, LIKE EVERYTHING ELSE ON THIS DISPLAY. the entrypoint is still root here and hands
# off to hmux-drop at the end, so anything started before that inherits root unless it is told not
# to -- and a window manager holding the display as root would be the one process on it that pi,
# chrome and every face are not. the setgroups guard is hmux-drop's, for the same reason: a
# keep-id userns denies setgroups(), so --init-groups fails there.
as_hmux() {
  if [ "$(id -u)" != 0 ]; then
    "$@"
  elif [ "$(cat /proc/self/setgroups 2>/dev/null)" = deny ]; then
    setpriv --reuid hmux --regid hmux "$@"
  else
    setpriv --reuid hmux --regid hmux --init-groups "$@"
  fi
}

# the window manager for OUR display: a titlebar to drag, a border to resize, buttons to maximize.
# without it chrome opens where it opened and stays there, which made the shared screen a page you
# could use and nothing around it.
#
# ONLY ON THE DISPLAY WE OWN -- the caller decides that, and the reason it matters is that the
# other case is a developer's forwarded desktop, which already has a window manager. a second one
# on the same screen fights the first over every window on it, including windows that are not ours.
#
# NOT SUPERVISED, deliberately: if it dies the display keeps working and windows stop being
# movable, which is where we were before. taking the container down over it would trade a stiff
# screen for no agent at all -- the same trade the display bootstrap refuses above.
# PERSONA_WM names the binary, or `none` to go back to a bare display.
start_window_manager() {
  wm="${PERSONA_WM:-jwm}"
  if [ "$wm" = none ] || [ -z "$wm" ]; then
    echo "persona: no window manager (PERSONA_WM=${PERSONA_WM:-none}); the canvas display stays bare"
    return 0
  fi
  command -v "$wm" >/dev/null 2>&1 || {
    echo "persona: no $wm in this image; the canvas display stays unmanaged" >&2
    return 0
  }
  as_hmux env DISPLAY="$1" "$wm" -f /etc/persona/jwmrc >/tmp/jwm.log 2>&1 &
  echo "persona: started $wm on $1"
}

if [ -n "${DISPLAY:-}" ] && [ "${DISPLAY}" != "${PERSONA_DISPLAY}" ] && ! answers "${DISPLAY}"; then
  echo "persona: the forwarded display ${DISPLAY} does not answer; using our own instead"
  DISPLAY=""
fi

if [ -z "${DISPLAY:-}" ] || [ "${DISPLAY}" = "${PERSONA_DISPLAY}" ]; then
  # A SOCKET IS NOT A SERVER, here for the second time and for a different reason: an X server that
  # dies does NOT unlink its socket, and this one's socket lives in the HOST's /tmp/.X11-unix (the
  # bind mount that makes a forwarded desktop work). so every container restart found its own dead
  # socket from the previous run, "adopted" it, and handed the stack a display nothing was
  # listening on -- the same broken end state as trusting a forwarded $DISPLAY, reached from the
  # opposite direction. ask, then clear it if it does not answer.
  ours="/tmp/.X11-unix/X${PERSONA_DISPLAY#:}"
  if [ -e "$ours" ] && ! answers "${PERSONA_DISPLAY}"; then
    echo "persona: ${PERSONA_DISPLAY} has a stale socket (no server); clearing it"
    rm -f "$ours" "/tmp/.X${PERSONA_DISPLAY#:}-lock" 2>/dev/null || true
  fi
  if [ -e "$ours" ]; then
    echo "persona: adopting the display already on ${PERSONA_DISPLAY}"
    # a restart inside a live container: the server survived, and so may the window manager it was
    # started with. asking is cheaper than a second one racing the first for the same screen.
    if pgrep -x "${PERSONA_WM:-jwm}" >/dev/null 2>&1; then
      echo "persona: a window manager is already running on ${PERSONA_DISPLAY}"
    else
      start_window_manager "${PERSONA_DISPLAY}"
    fi
  elif command -v Xvfb >/dev/null 2>&1; then
    # THE LOCK OUTLIVES THE SOCKET, BECAUSE THEY ARE IN DIFFERENT FILESYSTEMS. `/tmp/.X11-unix` is
    # the HOST bind mount; `/tmp/.X<n>-lock` is the container's own /tmp, which survives a
    # `podman restart` while the socket does not. The cleanup above removes them as a PAIR and is
    # gated on the socket, so a restart that leaves only the lock behind skips it -- and Xvfb then
    # refuses with "Server is already active for display 88", dies, and /canvas idles for the life
    # of the container with nothing in the hub's log to say why. Measured in production 2026-08-26.
    #
    # AND X CANNOT DETECT THIS STALENESS ITSELF, which is why the lock has to be removed rather
    # than left to it. X reads the pid out of the lock and asks whether it is ALIVE -- a lock
    # holding a DEAD pid is cleaned up by X automatically and is harmless. But a container restart
    # BEGINS PIDS AGAIN AT 1, so the pid the old Xvfb recorded is very likely alive in the new
    # namespace as a completely different process. X finds it, believes its server is still
    # running, and refuses. That is why this presents as INTERMITTENT: it depends on whether the
    # recorded pid happens to be reused. Reproduced both ways -- a dead-pid lock does NOT
    # reproduce it, a live-pid lock reproduces it every time.
    #
    # SAFE HERE AND NOT ABOVE: reaching this branch means the socket does not exist, so nothing was
    # adopted and no server is running on this display -- an X server always creates its socket. The
    # cleanup above cannot widen to `! answers` instead, because `answers` is FALSE when xdpyinfo is
    # merely absent, which would clear a live server's lock.
    rm -f "/tmp/.X${PERSONA_DISPLAY#:}-lock" 2>/dev/null || true
    Xvfb "${PERSONA_DISPLAY}" -screen 0 "${PERSONA_SCREEN}" -nolisten tcp >/tmp/xvfb.log 2>&1 &
    # WAITED FOR, not assumed: the canvas face opens the display at startup and an X server takes
    # a moment to bind its socket. the face idles rather than dying if it loses the race, but it
    # would idle for the life of the container.
    started=""
    for _ in $(seq 1 50); do
      [ -e "/tmp/.X11-unix/X${PERSONA_DISPLAY#:}" ] && { started=1; break; }
      sleep 0.1
    done
    # THE LOOP'S VERDICT WAS COMPUTED AND DISCARDED. This printed "started Xvfb" unconditionally,
    # so a server that never came up reported success -- which is exactly how the lock bug above
    # stayed invisible: the entrypoint said it started, `ps` said otherwise, and only Xvfb's own
    # log (which nothing reads) had the reason. Say what happened, and say where the reason is.
    if [ -n "$started" ]; then
      echo "persona: started Xvfb on ${PERSONA_DISPLAY} (${PERSONA_SCREEN})"
      # AFTER the wait above, not beside it: a window manager that loses the race to the X server's
      # socket exits immediately, and it would exit into a log nobody reads.
      start_window_manager "${PERSONA_DISPLAY}"
    else
      echo "persona: Xvfb did NOT come up on ${PERSONA_DISPLAY}; /canvas will idle. see /tmp/xvfb.log" >&2
      sed 's/^/persona: xvfb: /' /tmp/xvfb.log >&2 2>/dev/null || true
    fi
  else
    echo "persona: no Xvfb in this image; chrome stays headless and /canvas will idle" >&2
  fi
  export DISPLAY="${PERSONA_DISPLAY}"
else
  echo "persona: using the forwarded display ${DISPLAY}"
fi

# the setup above needs root (seed /work, write /data, read the /config mounts); hand off to the base
# image's hmux-drop, which chowns the volumes to `hmux` and drops privileges before exec'ing `hmux up
# persona`. so the whole agent -- pi, faces, chrome, bash -- runs unprivileged (uid 1000). see the
# hmux Dockerfile (33e) and browser-head, which no longer needs --no-sandbox as a result.
exec /usr/local/bin/hmux-drop "$@"   # drop to hmux, then -> hmux up persona
