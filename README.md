# PERSONA

the past is just a story we tell ourselves.

persona (or "Per") is an AI agent akin to the Claw family but with humbler goals.

it is built on HCP, a hook contract which decouples it from harness internals and gives
it the ability to self-modify. hmux runs those hooks as its `hcp` face, so the same
self-model composes across whichever harness is active.

persona runs fully sandboxed in a container, served by
[hmux](https://github.com/khimaros/hmux) as its single control plane: one image drives
`hmux up`, exposing a chat webui, a voice ui, and an opencode-compatible face for the tui.
the harness underneath is pi (canonical) or opencode.

persona has a heartbeat mechanism, a simple default SOUL.md, task tracking, and a journal.

[TAXONOMY.md](TAXONOMY.md) maps every concept across the three layers -- HCP the protocol, hmux
the host, persona the agent -- including which words mean different things in each.

it supports most models and providers as well as most subscriptions (for now).

## install

needs docker or podman (with the compose plugin).

### build and run

persona's image is `FROM khimaros/hmux`. get that base image first - pull it, or build it
from the [hmux](https://github.com/khimaros/hmux) repo (`cd ../hmux && make image`). then
build persona's image and start it:

```
make build
make up
```

use `make COMPOSE="podman compose" ...` for rootless podman.

podman compose is the default and needs no edits. on docker, delete the `userns_mode` line in
`docker-compose.yml` -- `keep-id` is a podman-only knob (it gives the container's `hmux` your
host uid for headed browsing); docker maps the container user to your host uid directly.

### configuring a deployment

copy `persona.env.example` to `persona.env` (gitignored) and edit it. that file is everything
the CONTAINER reads: api keys, the matrix bridge, the voice endpoint, and any `HMUX_CFG_*`
override of a `hmux/config.toml` setting -- which reaches every knob in that file, including
tables it does not mention, so a deployment should not need to edit the config itself.

what compose decides BEFORE the container exists -- published ports, where `/work` and `/data`
live, the uid -- cannot come from `persona.env`, because compose hands that file to the
container only after deciding all of it. those live in `docker-compose.yml`, each with a
comment; `/work` and `/data` have a commented host-directory alternative to the default volumes.

## model setup

### custom providers

the compose mounts your `~/.pi/agent/{models.json,settings.json,auth.json}` read-only and
the entrypoint copies them into the container (so providers, enabled models, defaults, and
auth all carry over). prepare them as usual (e.g. with `llama-update-models` from
[llama-tools](https://github.com/khimaros/llama-tools)). the provider host named in
`models.json` must be reachable from the container: set it under `extra_hosts` in
`docker-compose.yml` (or use `host.docker.internal`).

### hosted providers (oauth)

mount an existing `auth.json` into `/data/.local/share/opencode/`, or run the provider's
device-code flow once against the persisted `/data` volume.

## attach

### webui

opens the chat ui at `http://localhost:4280/webui`. every ui hangs off the hub's single
published port: the full renderer at `/webui`, a SIMPLIFIED chat ui -- sessions, a transcript
and a prompt box, nothing else -- at `/chat`, and voice at `/omni`.

```
make webui
```

each face can also be published on a port of its own; the lines are in `docker-compose.yml`
under `ports:`, commented out.

### tui

attaches an opencode tui to the opencode face. the opencode face has no hub mount, so this
needs its `:4096` publish uncommented in `docker-compose.yml` first.

```
make tui
```

### voice

the omni voice + text ui is at `http://localhost:4280/omni`. speech runs on the VOICE FACE, not
in omni: set `HMUX_VOICE_URL` in `persona.env` (it falls back to `OPENAI_BASE_URL`), else the
face idles and omni is text-only. see `persona.env.example`.

## chat

### per

use the **Per** agent for friendly conversation, browser use, and memory:

> hi, my name is hank and i'm from cincinatti

> modify your core operations to do something surreal whenever we talk

> what was that link you sent me yesterday?

see also: [browser use](#browser-use)

### admin

use the **Admin** agent for meta-administration:

> run all system updates and summarize all of the changelogs

> configure the server to listen on port 8080

### browser-use

browser-use runs headless in the container. the agent runs unprivileged (the whole stack drops to
the `hmux` user; see [HMUX.md](HMUX.md)), so chrome keeps its sandbox while browsing the open web:

> summarize the top 5 stories on hacker news

for headed, collaborative browsing, run `make browser-head`: the container already has your host
X11 socket + `$DISPLAY` (wired in `docker-compose.yml`), so this grants local X access and opens a
real chrome window on your desktop -- the same instance the agent drives (you can watch, or take
over to solve a captcha). it blocks until you close the window; `make browser-stop` closes the
session from another shell. needs an X11 display on the host (a linux desktop, or xwayland under
wayland) -- the container analog of the old VM's `ssh -X` path; with no reachable display it falls
back to headless. VNC would be the remote / non-X11 alternative.

### logs

```
make logs
```

or exec a shell: `docker compose exec persona bash`.

## operations

```
make up            # start (detached)
make down          # stop and remove
make restart       # restart the container
make build         # rebuild the persona image
make browser-head  # open a headed chrome on your desktop (shared with the agent)
make browser-stop  # close the shared browser session
```

## evals

run the persona eval suite against the running container (a native client to the hmux hub
on `:4280`):

```
make eval                       # full suite
make eval FILTER=TestTrait      # only matching tests (pytest -k)
make eval REPEAT=5              # repeat and aggregate flakiness
make eval MODEL=kairos/ornith-1.0-35b:Q8_0   # pin a model (provider/id from models.json)
make eval REASONING=high        # thinking level: off (default), low, medium, high
make eval-bridge                # matrix-bridge tests against a throwaway fake homeserver
make eval-browser               # browser-use tests (headless real browsing)
```

`make eval` resets the container's `/work` to the baked workspace seed, runs it
heartbeat-free, and drives `evals/persona_eval.py` as a native hub client over the
`:4280/ws` api. `MODEL` pins the model under test (a `provider/id` from your
`models.json`); `REASONING` sets the per-session thinking level -- `off` (default),
`low`, `medium`, or `high`. off is the calibrated default: reasoning tends to make a
model answer from its own reasoning rather than follow the injected persona
instructions. `MODEL`/`REASONING` apply to `make eval-bridge` and `make eval-browser` too.
