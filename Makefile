# persona runs as a single container: a thin image FROM khimaros/hmux:browser driving `hmux
# up persona` (pi backend + admin/omni/opencode faces). see HMUX.md.

# docker or podman; override e.g. `make COMPOSE="podman compose" up`.
COMPOSE ?= docker compose

# the published image (docker hub) and its ghcr mirror. TAG defaults to latest; override
# to cut a version alongside it: `make push TAG=v0.1.0`.
IMAGE     ?= khimaros/persona
TAG       ?= latest
DOCKERHUB ?= docker.io/$(IMAGE)
GHCR      ?= ghcr.io/$(IMAGE)

# eval model + selectors. default is the dense 27b at REASONING=off: a REPEAT=3 clean sweep
# showed it reliable (~30/31, ~7min/run), while the a3b MoE is high-variance (30/TIMEOUT/16)
# and minimax is perfect but slow (~15min). override MODEL to compare. reasoning defaults to
# off: it hurts instruction-following (see persona_eval.py).
MODEL?="kairos/qwen3.8-27b:Q8_0"
REASONING?=off
FILTER?=
REPEAT?=1
# throwaway compose project prefix for eval runs; each run appends a short random id
EVAL_PREFIX:=persona-eval-

# --- images / lifecycle ---

# persona mixes TWO images together (see the Dockerfile):
#   khimaros/browser-use  the base -- chrome, python3, the browser-use cli, and browser-head,
#                         which belongs to it (a browser with no way to drive it is half an
#                         interface). built HERE, from images/browser-use, which is self-contained.
#                         persona keeps the SKILL that documents the commands, not the scripts.
#   khimaros/hmux         copied in on top, then wired up by hmux's own /opt/hmux/hmux-install.
#                         built + published from the hmux repo (`cd ../hmux && make image`), or
#                         pulled. persona does not build it.
#
# the browser is the BASE rather than a layer on hmux because a layer is invalidated by anything
# beneath it: on top of hmux those ~1.4GB of never-changing packages were remade and re-uploaded
# on every single release (1575MB of a 2747MB image). it is also the only direction that composes
# -- hmux's payload is self-contained, chrome's is not.
BROWSER_USE_IMAGE ?= khimaros/browser-use

# the refs the persona build mixes from. a BARE `khimaros/hmux:latest` is ambiguous once a
# `docker.io/khimaros/hmux:latest` of the same name is also present locally -- which it is after
# any publish -- and podman resolved it to the older remote-tagged one, quietly mixing in an hmux
# whose payload predated hmux-install. so qualify with localhost/ under podman, where that names
# the locally-built image and nothing else.
LOCAL_PREFIX := $(shell docker --version 2>/dev/null | grep -qi podman && echo localhost/)
HMUX_REF         ?= $(LOCAL_PREFIX)khimaros/hmux:latest
BROWSER_USE_REF  ?= $(LOCAL_PREFIX)$(BROWSER_USE_IMAGE):latest

# the browser base. rebuilt only when chrome, browser-use or its own scripts change -- NOT on an
# hmux or persona change, which is the entire point. the directory is self-contained, so it is
# also the whole build context.
browser-use-image:
	docker build -t $(BROWSER_USE_IMAGE):latest images/browser-use
.PHONY: browser-use-image

# build persona's OCI image. `image` and `build` are the same target: `image` mirrors the
# hmux repo's name (and is what `publish` hangs off), `build` is the compose-lifecycle name.
# it does NOT depend on browser-use-image: that base is expensive and near-static, so it is built
# deliberately (`make browser-use-image`) rather than on every persona build.
image build:
	HMUX_IMAGE=$(HMUX_REF) BROWSER_USE_IMAGE=$(BROWSER_USE_REF) $(COMPOSE) build
.PHONY: image build

# compose loads persona.env with the SHORT `env_file` form (the compatible one), which requires the
# file to exist -- so seed it from the example, whose every line is commented and therefore changes
# nothing. a fresh clone comes up without an editing step.
#
# hmux/config.local.toml is seeded the same way and for the same reason -- a bind mount of a file
# that does not exist creates a DIRECTORY, and the config would then fail to parse. empty means the
# merge sets nothing, so a clone that never touches it behaves exactly as before.
up:
	@test -f persona.env || { cp persona.env.example persona.env; echo "seeded persona.env from persona.env.example"; }
	@test -f hmux/config.local.toml || { cp hmux/config.local.toml.example hmux/config.local.toml; echo "seeded hmux/config.local.toml from hmux/config.local.toml.example"; }
	$(COMPOSE) up -d
.PHONY: up

# there was a bind-mount "fast loop" here (host binary + client dists mounted over the image). it
# is GONE, and should not come back in that shape: it produced two silent, hard-to-read failures.
# mounting backends/ shadowed the image's copy with the host's, whose `.pi-sdk` symlink points into
# the host mise install -- pi died at startup and NOTHING registered, which surfaced as "waiting for
# harness to connect" in one client and an empty session list in another. and every client build
# does `rmSync("dist")` + `mkdirSync`, so a rebuild creates a NEW inode while the mount still points
# at the old one: the container serves an empty directory and the page 404s its own modules. both
# failures look like application bugs and cost more than the image build ever saved.
#
# so: build the image. `make image && make up`, and for a change to the hmux base (rust, backends,
# clients) `cd ../hmux && make image` first.

down:
	$(COMPOSE) down
.PHONY: down

restart:
	$(COMPOSE) restart persona
.PHONY: restart

logs:
	$(COMPOSE) logs -f persona
.PHONY: logs

# --- publish ---

# authenticate before the first publish. docker hub: your account + token. ghcr: your github
# USERNAME plus a classic PAT with the write:packages scope -- fine-grained tokens are not
# accepted by the container registry, and fail as an ordinary auth error rather than saying so.
login:
	docker login docker.io
	docker login ghcr.io
.PHONY: login

# build then push to docker hub under $(TAG). the amd64 chrome deb makes this an amd64-only
# image today (arm is a follow-up).
#
# GHCR IS OFF BY DEFAULT: nothing pulls the mirror today, and this image is ~2.75GB with NO blob
# reuse between pushes (every publish re-uploads the whole thing), so a second registry is a
# second full upload. that is a volume cost, not a bandwidth one -- the link measures 25Mbps while
# a push draws 2.5-4.8 -- but it is real, and the pushes below run concurrently precisely so the
# mirror costs wall-clock time only when it is asked for. `make publish PUSH_GHCR=1` includes it,
# which is worth doing on a release someone might actually pull from ghcr, since it is stale.
# where the hmux base image is built from. persona's payload (hub, faces, admin, hub-client, omni)
# all comes from there, and `docker build` CANNOT NOTICE a stale one -- the COPY succeeds either
# way -- so a publish silently ships a month-old client and looks exactly like a fresh one. that has
# happened; see the Dockerfile header.
HMUX_DIR ?= ../hmux

# the whole stack, in the only order that is correct: rebuild the base from source, then build and
# push persona on top of it. use this rather than a bare `make publish` whenever the change is in
# the hmux repo. (running the two by hand is how a base gets skipped, and chaining
# `cd $(HMUX_DIR) && make image && make publish` is worse -- the second make then publishes HMUX,
# from the wrong repo.)
publish-stack:
	$(MAKE) -C $(HMUX_DIR) image
	$(MAKE) publish
.PHONY: publish-stack

PUSH_GHCR ?=
publish: image
	docker tag $(IMAGE):latest $(DOCKERHUB):$(TAG)
	@if [ -n "$(PUSH_GHCR)" ]; then \
		echo "publishing to docker hub + ghcr"; \
		docker tag $(IMAGE):latest $(GHCR):$(TAG); \
		docker push $(DOCKERHUB):$(TAG) & dh=$$!; \
		docker push $(GHCR):$(TAG) & gh=$$!; \
		wait $$dh; rc_dh=$$?; \
		wait $$gh; rc_ghcr=$$?; \
		echo "push: dockerhub=$$rc_dh ghcr=$$rc_ghcr"; \
		[ $$rc_dh -eq 0 ] && [ $$rc_ghcr -eq 0 ]; \
	else \
		echo "skipping ghcr (PUSH_GHCR=1 to include it)"; \
		docker push $(DOCKERHUB):$(TAG); \
	fi
.PHONY: publish

# --- attach ---

# through the hub port, the only one published. PERSONA_HUB_PORT matches the compose publish.
admin:
	open http://localhost:$(or $(PERSONA_HUB_PORT),4280)/admin
.PHONY: admin

# the opencode face is NOT published by default and has no `expose` mount, so this needs the
# 4096 line in docker-compose.yml uncommented first (then `make down && make up`).
tui:
	opencode attach http://localhost:$(or $(PERSONA_FACE_PORT),4096)
.PHONY: tui

# --- headed browsing ---

# launch a HEADED chrome inside the RUNNING container that renders on your desktop's X server -- the
# same browser instance the agent drives over CDP (browser-use). the container analog of the old VM's
# `ssh -o ForwardX11=yes`: the X11 socket + $DISPLAY are already wired in by docker-compose.yml, so
# this just grants local clients access to your display (`+local:` covers both docker root and
# rootless-podman's uid-mapped root; idempotent, and left in place so it does not clobber a standing
# grant), then execs browser-head to start chrome and block until you close it. `--user hmux` runs it
# as the SAME unprivileged user the agent runs as (docker exec defaults to root), so the shared chrome
# + its /data/.chrome-profile stay hmux-owned and sandboxed. needs an X11 display on the host (a linux
# desktop, or xwayland under wayland). run `make up` first if not already up.
browser-head:
	xhost +local: >/dev/null 2>&1 || true
	clear && $(COMPOSE) exec --user hmux persona sh -c 'browser-head start && browser-head wait'
.PHONY: browser-head

# close the headed browser session (leaves the container running).
browser-stop:
	$(COMPOSE) exec --user hmux persona browser-head stop
.PHONY: browser-stop

# --- tests ---

# the hook contract test runs against the workspace on the host (no container needed), and the
# permission policy is checked against the real config.toml -- a path named in `tools` but not in
# `external` is refused at runtime, which is not visible by reading either list alone. the defaults
# test guards the other half: what a deployment must NOT have to edit, and what it must opt into.
# the display test is the only one here with an ARTIFACT half: it runs the built image and asks the
# window manager whether it is really managing the canvas display. that half SKIPS (loudly, in the
# summary line) when the image predates the Dockerfile, so `make test` on an unbuilt tree is honest
# rather than either red or falsely green.
test:
	python3 workspace/tests/persona_test.py
	python3 tests/permission_policy_test.py
	python3 tests/config_defaults_test.py
	python3 tests/display_test.py
.PHONY: test

# run the eval heartbeat-free against the hmux hub (persona_eval.py is a native hub client).
# each REPEAT run gets its OWN throwaway compose project + uniquely named container + fresh
# /work and /data volumes, so no state leaks between runs: run 1's onboarding deletes
# BOOTSTRAP.md, and a shared volume would make every later run's bootstrap tests fail
# spuriously. the entrypoint seeds a fresh volume from scratch, and `down -v` discards it
# after each run. the eval publishes the hub to a RANDOM free host port (PERSONA_HUB_PORT ->
# free_ports.py; the faces are not published at all and the eval only ever dials the hub) and only
# ever removes `persona-eval-` prefixed containers/volumes, so the developer's own `persona`
# container keeps running untouched on :4280.
# the eval NEVER inherits persona.env's real HMUX_BRIDGE_* creds: docker-compose.eval.yml overrides them
# with explicit safe values (empty here -> bridge idles; the fake homeserver in eval-bridge).
COMPOSE_EVAL:=$(COMPOSE) -f docker-compose.yml -f docker-compose.eval.yml
eval:
	-docker ps -aq --filter name=$(EVAL_PREFIX) | xargs -r docker rm -f
	-docker volume ls -q --filter name=$(EVAL_PREFIX) | xargs -r docker volume rm
	rm -f /tmp/eval-run-*.xml
	for run in $$(seq 1 $(REPEAT)); do \
		proj=$(EVAL_PREFIX)$$(od -An -tx1 -N4 /dev/urandom | tr -d ' \n'); \
		hubp=$$(cd evals && python3 free_ports.py 1); \
		echo "=== eval run $$run/$(REPEAT): fresh container + volumes ($$proj), hub on host :$$hubp ==="; \
		PERSONA_HUB_PORT=$$hubp \
		COMPOSE_PROJECT_NAME=$$proj PERSONA_CONTAINER=$$proj HMUX_HCP_HEARTBEAT_ENABLED=false \
			$(COMPOSE_EVAL) up -d persona \
			|| { echo "EVAL SETUP FAILED: the container did not start"; exit 1; }; \
		echo "waiting for hub + backend readiness (startup installs extension deps)..."; \
		ready=""; \
		for w in $$(seq 1 90); do \
			( cd evals && python3 wait_ready.py ws://localhost:$$hubp/ws ) && { echo "ready after $${w}s"; ready=1; break; }; \
			sleep 1; \
		done; \
		: $${ready:?"EVAL SETUP FAILED: the hub never became ready"}; \
		HUB_URL=ws://localhost:$$hubp/ws BACKEND_MODEL=$(MODEL) REASONING=$(REASONING) \
			pytest evals/persona_eval.py -v -W all -k "$(if $(FILTER),$(FILTER),not TestBrowserUse)" --junitxml=/tmp/eval-run-$$run.xml || true; \
		COMPOSE_PROJECT_NAME=$$proj PERSONA_CONTAINER=$$proj $(COMPOSE_EVAL) down -v 2>/dev/null || true; \
	done
	@if [ $(REPEAT) -gt 1 ]; then python3 evals/aggregate.py /tmp/eval-run-*.xml; fi
.PHONY: eval

# bridge variant: start a fake-matrix on the host and point the container's bridge at it (via
# host.containers.internal, mapped in docker-compose.yml). the persona's bridge tools go live and
# its sends are captured by the fake, so TestBridgeMessaging (gated on EVAL_BRIDGE) can exercise
# them without a live matrix account. needs ../fake-matrix built (make -C ../fake-matrix build).
FAKE_MATRIX_BIN:=../fake-matrix/target/debug/fake-matrix
FAKE_MATRIX_PORT?=8448
eval-bridge:
	@test -x $(FAKE_MATRIX_BIN) || { echo "build ../fake-matrix first: make -C ../fake-matrix build"; exit 1; }
	-docker ps -aq --filter name=$(EVAL_PREFIX) | xargs -r docker rm -f
	-docker volume ls -q --filter name=$(EVAL_PREFIX) | xargs -r docker volume rm
	set -e; \
	$(FAKE_MATRIX_BIN) --host 0.0.0.0 --port $(FAKE_MATRIX_PORT) >/tmp/eval-fake-matrix.log 2>&1 & \
	fmpid=$$!; trap 'kill $$fmpid 2>/dev/null' EXIT; \
	proj=$(EVAL_PREFIX)$$(od -An -tx1 -N4 /dev/urandom | tr -d ' \n'); \
	hubp=$$(cd evals && python3 free_ports.py 1); \
	echo "=== bridge eval ($$proj); hub on host :$$hubp; fake-matrix on host :$(FAKE_MATRIX_PORT) ==="; \
	PERSONA_HUB_PORT=$$hubp \
	COMPOSE_PROJECT_NAME=$$proj PERSONA_CONTAINER=$$proj HMUX_HCP_HEARTBEAT_ENABLED=false \
		HMUX_BRIDGE_HOMESERVER=http://host.containers.internal:$(FAKE_MATRIX_PORT) \
		HMUX_BRIDGE_USER_ID=@per:fake.local HMUX_BRIDGE_ACCESS_TOKEN=fake_token \
		$(COMPOSE_EVAL) up -d persona; \
	echo "waiting for hub + backend readiness..."; \
	for w in $$(seq 1 90); do \
		( cd evals && python3 wait_ready.py ws://localhost:$$hubp/ws ) && { echo "ready after $${w}s"; break; }; \
		sleep 1; \
	done; \
	echo "seeding an inbound DM so the bridge opens a session (bridge_rooms shows it; the send test needs the context)"; \
	curl -s -XPOST http://localhost:$(FAKE_MATRIX_PORT)/_fake/inject -H 'content-type: application/json' \
		-d '{"room_id":"!dm:fake.local","sender":"@tester:fake.local","body":"hey per, will you be around this evening?"}' >/dev/null || true; \
	sleep 10; \
	HUB_URL=ws://localhost:$$hubp/ws BACKEND_MODEL=$(MODEL) REASONING=$(REASONING) EVAL_BRIDGE=1 \
		pytest evals/persona_eval.py -v -W all -k TestBridgeMessaging --junitxml=/tmp/eval-bridge.xml || true; \
	COMPOSE_PROJECT_NAME=$$proj PERSONA_CONTAINER=$$proj $(COMPOSE_EVAL) down -v 2>/dev/null || true
.PHONY: eval-bridge

# browser-use is slow + model-heavy (real browsing), so it is excluded from `make eval`
# by default; run it on its own here.
eval-browser:
	$(MAKE) eval FILTER=TestBrowserUse
.PHONY: eval-browser

precommit: test
.PHONY: precommit
