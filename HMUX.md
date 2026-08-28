# hmux deployment (design notes)

persona's serving/control plane is hmux, deployed as one container. pi is canonical,
opencode optional. the hmux `hcp` FACE is a hub-client host
(`hmux face hcp --cwd /work`) that arms hmux's interception hooks and drives persona's
hook script (`/work/hooks/persona.py`, the HCP v3 contract), giving persona its prompt
composition, tools, and heartbeat across whichever backend is active. persona.py plus the
workspace ARE the hook implementation; hmux is the host. the three names are HCP (the
protocol) / hmux (the host) / persona (the agent).

the deployment files live at the repo root: `Dockerfile`, `docker-entrypoint.sh`,
`docker-compose.yml`, `hmux/config.toml`, and the `Makefile`. this doc is the rationale;
those files are the source of truth.

## how the hcp face works

`hmux up persona` starts the hub (auto), the pi backend, and seven faces: `hcp` (prompt
composition + tools + heartbeat), `bridge` (matrix), `permission` (the tool-call gate),
`admin` (the full browser ui), `chat` (the simplified one), `omni`, and `opencode`.

- the `hcp` face connects to the hub as a CLIENT, runs `discover` on the hook script, and
  arms hmux's system-prompt hook -- it composes the prompt from persona's SOUL + traits +
  prompts (the hook's `mutate_request` stage) and registers the hook's tools (19: the
  trait_/task_/data_/prompt_ families) plus the `hcp_*` face builtins (heartbeat status/set,
  hook read/write). a core-trait edit carries permission metadata (`@tool(permission=...)`) that the
  `permission` face can gate (the initial autonomy-safe policy does not yet; see the follow-up on
  arg-keyed core-trait gating).
- arming at the HUB is what makes it backend-agnostic: the same prompt + tool surface
  composes over pi and (for prompt interception) opencode at once. tool registration is
  generic on the pi backend; opencode caches plugin tools, so its tool registration needs an
  in-process MCP shim (M3, pending -- prompt interception already works there).
- git stays HOST-side: the hcp face commits `/work` when a hook or heartbeat changes it, so
  persona's self-edits are versioned without the hook owning git.
- the heartbeat is HOST-owned end to end. its agent + cleanup + DEFAULT cadence are the hcp
  client's config in `hmux/config.toml`; the hub schedules the beat and the face drives a
  `[heartbeat]`-prefixed turn (a fresh session per beat, old ones disposed). Per can enable/disable
  the beat and retune its cadence at runtime via `hcp_heartbeat_set`, and inspect it via
  `hcp_heartbeat_status` / `hcp_heartbeat_history` -- those changes persist under `/work/.hcp/` and
  survive a restart. set `HMUX_HCP_HEARTBEAT_ENABLED=false` (compose env) to force it off for eval/demo.
  the hook only returns the beat's prompt when fired; it declares nothing about scheduling.
- the `bridge` face (`hmux face bridge`) mirrors a hub session per matrix room and relays replies,
  replacing the pi-messenger-bridge extension. it is matrix-sdk native (real E2E, unlike the old
  hand-rolled sync), reads its behavior from the `bridge` client table in `hmux/config.toml`, and
  takes the homeserver / user / access token from `HMUX_BRIDGE_*` compose env (unset -> the face idles,
  so it is harmless when matrix is not configured). it registers agent tools (`bridge_send`,
  `bridge_send_direct`, `bridge_rooms`) and arms the system-prompt hook to add room context for
  bridged sessions. durable state (the matrix e2e store + room<->session map) lives under
  `/work/.hcp/bridge/`. it also relays a deferred permission ask to the room (a whitelisted
  `permission_users` member replies allow/deny), resolving the gate over matrix.
- the `permission` face (`hmux face permission`) is the hmux-native tool-call gate, replacing the
  @gotgenes/pi-permission-system extension. it arms the hub's permission hook and decides each tool
  call from the policy in the `permission` client table (`default` + per-tool + `bash`/`mcp`/`skill`/
  `external` rules): allow runs it, deny blocks it, ask DEFERS to a human (admin prompt / bridge ->
  matrix / eval), else fails closed. the hub turned its permission gate into a PLUGGABLE priority
  chain (a policy client decides or defers; humans are the fallback), so a different permission
  system is just a different client arming the hook. persona's initial policy is autonomy-safe
  (default allow + a few dangerous-bash denies); tighten it in `hmux/config.toml`, or -- for a rule
  that belongs to one box rather than the repo -- in `hmux/config.local.toml`, which is merged over
  it at startup. that file is the only place a glob key can be written: `HMUX_CFG_*` reaches every
  scalar in the config, and a `*` is not legal in an environment variable name.

pi-evolve, the messenger-bridge, and the permission-system are all GONE as pi extensions -- they are
the `hcp`, `bridge`, and `permission` FACES now. persona ships no pi extensions.

## topology + ports

`hmux up persona` binds the hub on 0.0.0.0:4280 and the faces on 0.0.0.0 (via the base
image's `HMUX_LISTEN_HOST`); docker-compose publishes each on the matching host port. remap
in `docker-compose.yml` if a port is taken.

| role          | port (container = published) | through the hub port |
|---------------|------------------------------|----------------------|
| hub           | 4280                         | --                   |
| admin         | 4282                         | `/admin`             |
| simple chat   | 4290                         | `/chat`              |
| voice omni    | 4284                         | `/omni`              |
| voice face    | 4288                         | `/voice` (ws only)   |
| opencode face | 4096                         | --                   |

the admin rewrites the wildcard hub host to the page host so the browser dials the right
place (hmux 33b). the eval is a native hub client on 4280; the opencode face on 4096 serves
the tui (`opencode attach`).

## volumes + provider config

- `persona-work` -> `/work`: the workspace (traits/hooks/prompts/config). seeded from the
  baked `/opt/persona/workspace-seed` on first boot by the entrypoint, then mutable +
  git-versioned (persona self-edits it; the hcp face commits changes).
- `persona-data` -> `/data`: HOME. pi config, auth, and sessions persist here (no pi extensions
  anymore -- hcp/bridge/permission are hmux faces).
- `~/.pi/agent/{models.json,settings.json,auth.json}` -> `/config/*` (read-only): the pi
  agent config (providers, ENABLED models + defaults, auth). the entrypoint copies them into
  `/data/.pi/agent/`. the provider host named in models.json must be reachable from the
  container -- set it under compose `extra_hosts` (or use `host.docker.internal`). NOTE:
  settings.json's `enabledModels` is required; with only models.json the backend exposes no
  models and session create 502s.
- opencode oauth providers (optional): mount an auth.json into `/data/.local/share/opencode/`
  or run the device-code flow once against the persisted `/data` volume.

pi installs each extension's node deps on first session create, so the FIRST create on a
cold container 502s until that finishes (a few seconds); `make eval` waits for readiness
before driving. this image also replaces the base's mise-wrapped `npm` with a plain wrapper
(the wrapped one misresolves npm-cli.js and needs an absent `mise`), since pi's loader
spawns `npm` on session start.

## unprivileged runtime

the whole agent -- the pi backend, the hcp/bridge/permission/admin/omni/opencode faces, any bash it
runs, and chrome -- runs as the unprivileged `hmux` user (uid 1000), not root. the base image
(`../hmux`, 33e) creates that user and ships `/usr/local/bin/hmux-drop`, an entrypoint that works two
ways depending on how the container is started (persona's `docker-entrypoint.sh` does its own setup
then `exec hmux-drop "$@"` to reuse it):

- **rootless podman (the default here): run AS `hmux`, mapped to your host uid.** the compose sets
  `user: 1000` + `userns_mode: keep-id`, so the container's `hmux` IS you on the host. the host-owned
  volumes + config mounts are already ours (nothing to chown), and -- critically -- a non-root chrome
  can reach the host X11 socket (owned by you, mode 0775) for HEADED browsing. `hmux-drop` sees it is
  already unprivileged and just execs. without keep-id, `hmux` would map to a subuid that cannot even
  `connect()` the socket.
- **docker / no keep-id: start as root, drop.** the volumes are root-owned and the read-only `/config`
  mounts appear root-owned (the 600 auth.json needs a root read), so the entrypoint seeds `/work` +
  copies config as root, then `hmux-drop` chowns both volumes to `hmux` (unconditional + full-tree,
  since config is re-copied every boot) and drops via setpriv before `hmux up`.

running non-root restores chrome's sandbox for browsing (no `--no-sandbox`); the container supports it
(unprivileged user namespaces + the setuid chrome-sandbox helper both work). `make browser-head` execs
`--user hmux` so the shared browser + its `/data/.chrome-profile` stay hmux-owned.

## prerequisites

only to BUILD. running persona needs nothing but the published `khimaros/persona:latest`
(docker hub) and a container runtime; see the README's getting started.

the base image `khimaros/hmux` (persona's image is FROM it) is built + published from the
hmux repo (`cd ../hmux && make image`, or `make publish`), or pulled from a registry --
persona does not build it. then `make build && make up`.

## retired by the container migration

the incus VM stack is gone: incant.yaml, incant.env, incant-user.sh (shim_pkg/
write_manifest/place_plugin, install_pi*, install_opencode/plugins), the systemd units
(opencode/pi-serve/pi-webui/pi-omni), screenrc, and the VM Makefile. pi-evolve and
opencode-evolve (the two embedded HCP hosts) are gone too -- hmux's `hcp` face is the single
host now. hmux is the one front door via `docker compose`.

## follow-ups (not blocking)

- M3: opencode tool registration via an in-process http MCP server in the opencode backend
  (opencode caches plugin tools but re-resolves MCP tools per turn); prompt interception on
  opencode already works via the face.
- tighten the `permission` face policy (the initial one is autonomy-safe = default allow + a few
  dangerous-bash denies). fuller @gotgenes parity wants session-context-aware asks (autonomous vs
  interactive) and arg-keyed core-trait gating (gate only CORE trait edits, not every trait tool).
- browser-use headed/collaborative browsing: docker-compose.yml forwards the host X11 socket +
  $DISPLAY into the container, so `make browser-head` (xhost grant + exec) opens a headed chrome on
  your desktop -- the container analog of the old VM's `ssh -X`. the browser-head script falls back
  to headless when no display is reachable, so headless browsing still works (the eval forces it via
  an empty DISPLAY). VNC remains the follow-up for remote / non-X11 hosts.
- fold the HMUX_VOICE_* env into the hmux profile if hmux adds per-client env to the toml;
  today persona.env carries it (the heartbeat config already moved into the compose).

## validation checklist

1. (base image present) `make build && make up`; `make logs` shows hub + pi backend + faces
   up and the `hcp face: arming` line (persona hooks loaded).
2. admin: `make admin` (http://localhost:4280/admin) -> chat with Per; SOUL/traits in context,
   trait_*/task_* tools work, a core-trait edit prompts permission in the admin.
3. tui: `make tui` (opencode attach http://localhost:4096) -> same session visible (R7).
4. omni: http://localhost:4284 -> voice round-trip (with HMUX_VOICE_URL set).
5. eval: `make eval` -> suite green (a native hub client on :4280/ws drives session
   create/prompt + resolves permission/question gates).
