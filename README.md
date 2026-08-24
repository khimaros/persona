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

[TAXONOMY.md](https://github.com/khimaros/hmux/blob/master/TAXONOMY.md) (in the hmux repo) maps
every concept across the three layers -- HCP the protocol, hmux the host, persona the agent --
including which words mean different things in each.

it supports most models and providers as well as most subscriptions (for now).

## getting started

persona ships as a published container image: **`khimaros/persona:latest`** on docker hub
(`docker.io/khimaros/persona`), mirrored less often to `ghcr.io/khimaros/persona`. amd64 only
today. you need docker or podman and nothing else -- no checkout, no build, no toolchain.

### 1. give it a model

persona's pi backend reads its providers from `~/.pi/agent/models.json`,
`~/.pi/agent/settings.json` (enabled models + defaults) and `~/.pi/agent/auth.json` (keys and
oauth) -- the same files the pi cli uses. every recipe below mounts those three read-only at
`/config`, and the entrypoint copies them into the container's `/data` on each boot. prepare
them as usual first, e.g. with `llama-update-models` from
[llama-tools](https://github.com/khimaros/llama-tools).

a provider running on your own machine must be reachable FROM the container. add
`--add-host=host.docker.internal:host-gateway` to the run commands (or `extra_hosts:` in
compose) and name that host in `models.json`.

persona starts without any of this -- the portal comes up, it just has no model to talk to --
but DROP THE THREE MOUNTS if the files do not exist yet: podman refuses a bind mount whose
source is missing, and docker silently creates a directory where a file was meant to be. see
[model setup](#model-setup) for hosted providers and oauth.

### 2. run it

pick a runtime. all four do the same thing: one container, one published port (4280), and two
named volumes -- `/work`, the workspace persona self-edits, and `/data`, its home (config,
auth, sessions). both survive `rm` and re-`run`, so an upgrade keeps everything.

#### docker

```
docker run -d --name persona --restart unless-stopped \
  -p 4280:4280 \
  -v persona-work:/work \
  -v persona-data:/data \
  -v ~/.pi/agent/models.json:/config/models.json:ro \
  -v ~/.pi/agent/settings.json:/config/settings.json:ro \
  -v ~/.pi/agent/auth.json:/config/auth.json:ro \
  docker.io/khimaros/persona:latest
```

#### podman

the same command, verbatim -- rootless. the registry is spelled out because podman does not
assume one:

```
podman run -d --name persona --restart unless-stopped \
  -p 4280:4280 \
  -v persona-work:/work \
  -v persona-data:/data \
  -v ~/.pi/agent/models.json:/config/models.json:ro \
  -v ~/.pi/agent/settings.json:/config/settings.json:ro \
  -v ~/.pi/agent/auth.json:/config/auth.json:ro \
  docker.io/khimaros/persona:latest
```

`--restart` holds while the machine is up; to start at boot, wrap it in a quadlet unit.

#### docker compose

save this as `compose.yaml` in a directory of its own -- it names the published image, so it
needs no checkout and never builds:

```yaml
services:
  persona:
    image: docker.io/khimaros/persona:latest
    container_name: persona
    restart: unless-stopped
    ports:
      - "4280:4280"          # the hub. every ui hangs off this one port
    volumes:
      - persona-work:/work   # workspace: traits, hooks, prompts, tasks, journal
      - persona-data:/data   # HOME: pi config, auth, sessions
      - ${HOME}/.pi/agent/models.json:/config/models.json:ro
      - ${HOME}/.pi/agent/settings.json:/config/settings.json:ro
      - ${HOME}/.pi/agent/auth.json:/config/auth.json:ro
    # api keys, the voice endpoint, the matrix bridge, any HMUX_CFG_* override; see
    # persona.env.example in the repo. uncomment once the file exists.
    #env_file: ./persona.env

volumes:
  persona-work:
  persona-data:
```

then:

```
docker compose up -d
```

#### podman compose

the same file, the same commands:

```
podman compose up -d
```

### 3. open the portal

```
http://localhost:4280/
```

the hub root is the **portal**: a launcher listing every face persona serves, each a large tap
target with a live online dot. everything reaches persona through that single port:

- `/webui` -- the full chat ui: tools, thinking, permission prompts
- `/chat` -- a simplified chat: sessions, a transcript, a prompt box
- `/omni` -- voice and text
- `/touch` -- the ambient display

opening a face FROM the portal starts a FRESH session. reaching the same page any other way --
a bookmark, a reload, the back button -- resumes the session you were last in. a face that is
merely offline stays listed and greyed out; the voice face carries a machine protocol rather
than a page, so the portal does not list it.

say hi in `/webui` and Per introduces itself. see [chat](#chat) for what to ask it.

### 4. keep it off the open internet

persona TRUSTS the identity its front door reports, because a persona is a thing PEOPLE talk
to and one that cannot tell them apart addresses everyone as its single human. hmux has no
auth of its own, so on a directly-reachable hub those headers are just headers the caller
chose. publish 4280 on localhost or a private network, or put an authenticating proxy in
front of it. to turn the trust off instead, set in the container's environment:

```
HMUX_CFG_profile__persona__identity__trust_headers=false
```

### updating

the volumes hold everything persona has become, and none of it lives in the image:

```
docker pull docker.io/khimaros/persona:latest
docker rm -f persona && docker run -d ...      # the same run command as above
```

under compose, `docker compose pull && docker compose up -d` (or the `podman compose` pair).

## configuring a deployment

copy [persona.env.example](persona.env.example) to `persona.env` and point the container at it
(`--env-file persona.env`, or `env_file:` in compose). without a checkout, fetch it:
`curl -O https://raw.githubusercontent.com/khimaros/persona/master/persona.env.example`. every
line in it is commented, so an unedited copy changes nothing. that file is everything the CONTAINER
reads: api keys, the matrix bridge, the voice endpoint, and any `HMUX_CFG_*` override of an
`hmux/config.toml` setting -- which reaches every knob in that file, including tables it does
not mention, so a deployment should not need to edit the config itself.

what the runtime decides BEFORE the container exists -- published ports, where `/work` and
`/data` live, the uid -- cannot come from `persona.env`, because it is handed to the container
only after all of that is settled. those are flags on the run command, or the `ports:` /
`volumes:` / `user:` keys in compose.

`/work` and `/data` are named volumes by default. a deployment that wants them on the HOST --
editable and backed up without going through the container -- swaps in bind mounts
(`-v ./workspace:/work -v ./data:/data`).

## model setup

### custom providers

as in [step 1](#1-give-it-a-model): the three `~/.pi/agent` files are mounted read-only at
`/config` and copied into `/data` at boot, so providers, enabled models, defaults, and auth all
carry over. the provider host named in `models.json` must be reachable from the container.

### hosted providers (oauth)

mount an existing `auth.json` into `/data/.local/share/opencode/`, or run the provider's
device-code flow once against the persisted `/data` volume.

## the faces

every ui hangs off the hub's single published port and is listed on the portal. each face can
also be published on a port of its own -- add `-p 4282:4282` (webui), `4284` (omni), `4290`
(chat) -- but there is rarely a reason to.

### webui

`http://localhost:4280/webui` -- the full renderer: tool calls, thinking, permission prompts.
`/chat` is the same conversation through a plainer surface.

### touch

`http://localhost:4280/touch` -- the ambient display. the face serving it also gives Per the
`touch_*` tools, so it furnishes the room it lives in, and what it adds survives a restart.

### voice

the omni voice + text ui is at `http://localhost:4280/omni`. speech runs on the VOICE FACE, not
in omni: set `HMUX_VOICE_URL` in `persona.env` (it falls back to `OPENAI_BASE_URL`), else the
face idles and omni is text-only. see [persona.env.example](persona.env.example).

### tui

an opencode tui attaches to the opencode face. that face has no portal mount -- `opencode
attach` dials a base url, which a path-prefixed proxy has not been tested against -- so publish
its port (`-p 4096:4096`, or a `ports:` line in compose) and then:

```
opencode attach http://localhost:4096
```

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

browser-use runs headless in the container. the agent runs unprivileged (the whole stack drops
to the `hmux` user; see [HMUX.md](HMUX.md)), so chrome keeps its sandbox while browsing the
open web:

> summarize the top 5 stories on hacker news

for headed, collaborative browsing the container needs your host X11 socket and `$DISPLAY`
(`-v /tmp/.X11-unix:/tmp/.X11-unix -e DISPLAY`), and under rootless podman it must run as your
own uid so a sandboxed chrome can reach that socket (`--user 1000:1000
--userns=keep-id:uid=1000,gid=1000`). the checked-in `docker-compose.yml` wires all of it up;
with it running, `make browser-head` grants local X access and opens a real chrome window on
your desktop -- the same instance the agent drives, so you can watch, or take over to solve a
captcha. it blocks until you close the window; `make browser-stop` closes the session from
another shell. with no reachable display it falls back to headless. VNC would be the remote /
non-X11 alternative.

### logs

```
docker logs -f persona
```

or exec a shell: `docker exec -it persona bash`.

## operations

```
docker start persona     # start
docker stop persona      # stop
docker restart persona   # restart
docker rm -f persona     # remove the container (the volumes stay)
```

under compose: `up -d`, `stop`, `restart`, `down` (add `-v` only to destroy the volumes with
it). from a checkout the Makefile wraps the same lifecycle -- see below.

## from a checkout

the repo is only needed to BUILD persona, to run the evals, or to use the `make` targets. clone
it, then:

```
make up            # start (detached); seeds persona.env from the example on first run
make down          # stop and remove
make restart       # restart the container
make logs          # follow the logs
make webui         # open the ui
make browser-head  # open a headed chrome on your desktop (shared with the agent)
make browser-stop  # close the shared browser session
```

`make` drives [docker-compose.yml](docker-compose.yml), which carries the same shape as the
compose file above plus the X11 forwarding, the config mount, and comments on every knob. it
defaults to `docker compose`; use `make COMPOSE="podman compose" ...` for podman. that file
is written for rootless podman -- on docker, delete the `userns_mode` line, since `keep-id` is
a podman-only knob (docker maps the container user to your host uid directly).

to run the PUBLISHED image from the checkout rather than building one:

```
docker compose pull && docker compose up -d
```

### building

persona's image mixes two others: `khimaros/browser-use` (built here, from `images/browser-use`)
as the base, and the payload of `khimaros/hmux` copied in on top. get the hmux image first --
pull it, or build it from the [hmux](https://github.com/khimaros/hmux) repo
(`cd ../hmux && make image`) -- then:

```
make build
make up
```

## evals

run the persona eval suite against a throwaway container (a native client to the hmux hub):

```
make eval                       # full suite
make eval FILTER=TestTrait      # only matching tests (pytest -k)
make eval REPEAT=5              # repeat and aggregate flakiness
make eval MODEL=kairos/ornith-1.0-35b:Q8_0   # pin a model (provider/id from models.json)
make eval REASONING=high        # thinking level: off (default), low, medium, high
make eval-bridge                # matrix-bridge tests against a throwaway fake homeserver
make eval-browser               # browser-use tests (headless real browsing)
```

each run gets its own container, its own random host port and fresh volumes, so it neither
leaks state between runs nor disturbs a persona you already have up. it runs heartbeat-free
and drives `evals/persona_eval.py` as a native hub client over the `:4280/ws` api. `MODEL`
pins the model under test (a `provider/id` from your `models.json`); `REASONING` sets the
per-session thinking level -- `off` (default), `low`, `medium`, or `high`. off is the
calibrated default: reasoning tends to make a model answer from its own reasoning rather than
follow the injected persona instructions. `MODEL`/`REASONING` apply to `make eval-bridge` and
`make eval-browser` too.
