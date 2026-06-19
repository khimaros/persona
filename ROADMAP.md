# ROADMAP

```
[x] include pi-serve in the vm and point the eval BACKEND=pi at it over HTTP
    [x] add pi-serve.service (4096), drop the pi-webui websocket eval workarounds (piws.py)
    [x] reassign ports: pi-serve 4096, pi-webui 4092, pi-omni 4094; make webui -> 4092
    [x] only pi-evolve is a registered pi extension; pi-webui/pi-omni/pi-serve launch via binary
    [x] move persona.env to a neutral ~/.config/evolve/persona.env (both evolve stacks)
[x] eval support for BACKEND=pi via pi-webui websocket (backend abstraction in persona_eval.py)
[~] adopt hmux as the single serving/control plane, on containers (pi canonical, opencode optional) -- see HMUX.md
    substrate: a thin persona image FROM hmux:latest + a compose file REPLACE incus/incant/systemd. evolve
    (pi-evolve + persona.py) co-loads UNCHANGED with hmux tier-2 hooks left unarmed (no phase-3/hmux-31 work).
    [x] flatten server/ into the repo root; retire incant.yaml/incant.env/incant-user.sh/systemd/screenrc + VM Makefile
    [x] Dockerfile: FROM hmux:latest, bake pi-evolve/permission/messenger (npm -g) + browser-use, seed workspace
    [x] docker-entrypoint.sh: runtime-register pi extensions into /data (HOME volume masks build writes), seed /work, models.json
    [x] hmux/config.toml persona profile (backends=[pi], cwd=/work, clients=webui/omni/opencode)
    [x] docker-compose.yml: publish 4280 hub, 4092->4282 webui, 4094->4284 omni, 4096 face; volumes persona-work/persona-data + config + models.json
    [x] Makefile: container targets (hmux-image/build/up/down/restart/logs/webui/tui); eval drives the opencode face on localhost:4096
    [x] update README (container workflow) + HMUX.md (design notes pointing at the root files)
    [x] hmux 32d: opencode-face /session/status + permission/question reply coverage (compliance tests + fix landed in ../hmux)
    [x] validate build + run: persona:latest builds; container up in 3s; opencode face /global/health + /session/status + /agent respond; hub + pi backend + webui/omni/opencode faces all listening; pi extensions registered
    [x] validate a real turn against a provider (mounted ~/.pi/agent config): `per` replied in persona voice via kairos/qwen3.6-27b, evolve composed SOUL, /session/status cycled busy->idle live
    [x] entrypoint copies models.json + settings.json + auth.json from /config; fix npm on PATH (base symlink misresolves) so pi's per-session extension dep install works; Makefile eval waits for create-readiness (cold start installs deps)
    [x] eval green through the compose path: `make eval FILTER=TestTraitLifecycle` = 8 passed / 0 failed (was 4/4 before hmux 32e); fixed hmux 32e (live pi tool-output unwrap), rebuilt base+persona image, re-ran green
    [x] full `make eval`: 30/34 core tests GREEN (bootstrap/trait/task/recurring/data/journal all pass) through the compose path
    [ ] model-dependent eval fails (NOT the container/transport -- all models drive it correctly): qwen3.6-27b 30/31 (TestCoreExpansion too slow/verbose, hangs); qwen3.5-9b 29/31 in 6.5min (fails TestBootstrap test_01 + TestDataLifecycle test_05, answered from context); qwen3.6-35b-a3b 30/31 in 4.6min (fastest + most accurate, only fail is TestTaskLifecycle test_03 filter_by_due_date). prompt/model tuning per test
    [x] move general skills (browser-use, debian-maintenance) to a common top-level skills/; opencode-operate stays under opencode/skills/ (opencode-specific)
    [x] install skills into the container: common (browser-use/debian-maintenance) linked into BOTH ~/.pi/agent/skills and ~/.config/opencode/skills (work with either backend); opencode-operate only in opencode's dir; browser-head/matrix-login on PATH
    [x] real google-chrome in the image (deb, like old incant) + browser-head adds --no-sandbox/--disable-dev-shm-usage as root -> launches chrome headless in-container
    [x] browser-use working in-container: pinned browser-use[cli] @ git 8fc4f34a (the skill's `connect` subcommand landed on upstream main AFTER 0.12.6; published 0.12.6 has only a --connect flag). verified end to end: browser-head start -> connected; browser-use open example.com -> navigated; state -> live page read
    [x] TestBrowserUse test_01 (browser-head start) + test_02 (browser-use open HN) PASS -- Per drives browser-use end to end in the container. test_03 (visit 3 HN threads, 7+ browser-use calls, summarize to a trait) TIMES OUT on the high-thinking 27b (slow real browsing, not a functional failure)
    [x] default-exclude browser-use from `make eval` (-k "not TestBrowserUse"); added `make eval-browser` to run it on its own
    [x] port the eval off the opencode wire to the hmux NATIVE hub api (harness-agnostic; drops the opencode face from the eval path). evals/hub.py = vendored stdlib hub client; persona_eval.py drives session.create/subscribe/set_model + prompt, builds Response from normalized tool_update/text_delta/reasoning_delta events, resolves interceptor gates/elicitations, and waits on status.changed (R16); Makefile uses HUB_URL ws://:4280/ws + hub-based readiness (evals/wait_ready.py), dropped BACKEND_URL/BACKEND_DIR/x-opencode-directory. assertion helpers + test bodies UNCHANGED. validated at full scale: full core suite 30/31 via the native api (qwen3.6-35b-a3b, 4.6min; the 1 fail is model behavior)
    [x] calibrate the eval reasoning level (REASONING env -> session.set_reasoning). swept qwen3.6-35b-a3b off/low/medium/high (23/1/14/1) then re-ran the curated suite at off vs the earlier high: qwen3.6-27b 27->30/31, qwen3.6-35b-a3b 14->29/31, gpt-oss-20b 24~=23/31, qwythos3.5-9b 15->2/31, minimax-m2.7 21->31/31 (perfect, top model). finding: reasoning makes a model substitute its own reasoned-out reply for the injected persona instructions, which (via the stateful bootstrap cascade) collapses instruction-following; off is best for the qwen3.6 default model + neutral on gpt-oss. exception: qwythos (reasoning-tuned) NEEDS reasoning, collapses at off. changed the default to off in persona_eval.py + Makefile (override REASONING per-model when benchmarking a reasoning-native model)
    [x] eval isolation: give each REPEAT run its OWN throwaway compose project + uniquely
        named container + FRESH /work and /data volumes. was: force-recreate once, then loop
        pytest against one shared container -- run 1's onboarding deletes BOOTSTRAP.md, so
        every later run's bootstrap + stateful tests failed spuriously (qwen 87->92/93 once
        isolated; the "flaky" bootstrap pair was the leak, not the model). Makefile eval now
        loops `down -v` + `up` per run; docker-compose container_name is ${PERSONA_CONTAINER:-persona}.
        fixed a latent entrypoint bug this exposed: the /config->/data config copy ran before
        mkdir'ing /data/.pi/agent, so a truly FRESH /data volume crash-looped (cp: No such file
        or directory) -- a clean persona deploy would have hit the same bug
    [x] eval no longer tramples the dev `persona` container: publish the eval container's
        hub/faces to RANDOM free host ports (docker-compose ports are ${PERSONA_*_PORT:-default};
        evals/free_ports.py picks 4 free ports, the Makefile threads them + the hub port into
        wait_ready/pytest) and drop the up-front `compose down`. cleanup stays scoped to the
        `persona-eval-` prefix, so `make up`'s persona keeps the standard 4280/4282/4284/4096
        and runs alongside the eval
    [x] model sweep: eval 3x at REASONING off+high across gemma-4 (12b/26b-a4b/31b/e2b/e4b),
        qwen3.6 (27b/35b-a3b), gpt-oss (20b/120b) -- 18 valid combos, ~5h. off-leaders (mean/31):
        gemma-4-31b 31.0 (perfect 3/3), gpt-oss-120b 30.7, qwen3.6-27b 29.7, gpt-oss-20b + gemma-4-12b
        29.0. off>=high nearly everywhere; gpt-oss is reasoning-NEUTRAL (20b 29.0/29.0, 120b 30.7/28.3,
        zero meltdowns) -- most robust family. instability = the a3b/a4b MoEs, esp at high: qwen3.6-27b
        high is bimodal (30/3/30, ~1-in-3 full collapse); qwen3.6-35b-a3b unstable even at off (30/25/1)
        and DNF at high (could not finish 1 run inside the 90-min backstop). dense mid/large >> low-active
        MoE + edge (e2b/e4b/26b-a4b all cluster 21-23). ran alongside dev persona via the randomized ports
    [x] qwythos3.5-9b sweep: added the missing kairos entry to ~/.pi/agent/models.json (cloned from
        qwen3.5-9b, reasoning=true) and re-ran. it loads + drives tools (trait-only smoke 8/8 at high) but
        COLLAPSES on the full stateful suite: off 4.3/31 (1/11/1), high 1.3/31 (1/1/2) -- WORSE at high,
        and far below the old calibration's 15/31. passes only the no-tool CoreExpansion answer + an
        occasional bootstrap; fails the multi-step tool-driving flow. open q: genuine weakness vs a wrong
        compat/thinkingFormat (cloned qwen3.5-9b's max_completion_tokens + qwen-chat-template)
    [ ] bridge messaging evals: add a FAKE matrix homeserver (mirror the fake-openai e2e strategy)
        so the bridge face runs without a real matrix server. only ~7 plain client-server endpoints
        needed (versions; whoami -- device_id REQUIRED; keys/upload + keys/query; sync long-poll;
        room send; typing). e2e is compiled into matrix-sdk with no off-switch but only fires on rooms
        carrying m.room.encryption state, so present ONLY unencrypted rooms to skip olm/megolm (just
        accept the first-sync device-key upload). flow: inject a message -> bridge sees it via /sync ->
        drives the `per` agent on the hub -> reply captured at /send -> assert. gotchas: inbound
        origin_server_ts must be within 30s (else dropped); DM = m.direct account data + one non-bot
        member. cover DM round-trip + non-DM room-mention (trigger gate). BRIDGE_* env points the
        bridge at the fake (persona.env / the eval sets it)
    [ ] validate webui chat, voice, tui attach
    [x] restore `make browser-head`: the old VM target was `ssh -o ForwardX11=yes` -> the container
        analog is sharing the host X11 socket. docker-compose.yml mounts /tmp/.X11-unix + forwards
        $DISPLAY, so `make browser-head` just grants local X access (xhost) and execs `browser-head
        start && browser-head wait` in the running container -> chrome renders on the user's desktop,
        the same instance the agent drives over CDP. `make browser-stop` closes it. the browser-head
        script falls back to headless when no display is reachable (so headless browsing does not
        regress); the eval forces headless via an empty DISPLAY override. VNC stays the follow-up for
        remote / non-X11 hosts
    [~] later: retire evolve TS bridge by re-homing persona.py onto hmux interception -- needs hmux 31c (generic client tools)
[~] publish the image to docker hub + ghcr as khimaros/persona
    [x] rename the image persona:latest -> khimaros/persona (compose + `make publish`/`make login`); .dockerignore keeps host secrets (evolve/persona.env, models.json) out of the build context
    [x] mirror the publish tooling into hmux (`make publish`/`make login`/`make image`); hmux already had the OCI source label + .dockerignore
    [x] OCI image.source label so the ghcr package links back to the repo; description/licenses labels too
    [x] pin persona's pi extensions (pi-evolve 0.2.0, pi-permission-system 18.0.2, pi-messenger-bridge 0.4.0) so a rebuild does not drift from the base image's pi 0.80.2 (browser-use was already commit-pinned)
    [ ] pin FROM khimaros/hmux to a published digest/version -- BLOCKED: hmux is local-only today (`:latest` is a moving tag, not in any registry). requires publishing hmux first, then persona rides a fixed base
    [ ] arm64 / multi-arch (needs an arm64 google-chrome deb + `docker buildx --platform`) -- wanted eventually, not urgent; amd64-only for now
    [ ] optional: github actions workflow to build+push on a version tag (docker/login-action + build-push-action)
[~] retire the two embedded HCP hosts (pi-evolve/opencode-evolve); run persona on hmux's single RUST HCP-host
    face (../hmux phase 31). persona is a pure consumer now: workspace + persona.py hook. "evolve" retires as
    a concept (may redefine later); the three names are HCP (protocol) / hmux (host) / persona (agent).
    [x] persona.py: reads own prompts (load_prompts, dropped the ctx.prompts arg); added persona_prompt_*
        tools; declares heartbeat cadence + test via discover; heartbeat runs logged to state/heartbeat.jsonl;
        compose stays hook-side, cache-freeze host-side. contract test 483/483.
    [x] renamed off the evolve layer: config/evolve.jsonc -> config/persona.jsonc (+ entrypoint/AGENTS.md refs);
        state/evolve.json gone (model/heartbeat -> hub); persona.env already unwired (VM-era).
    [x] deployment: dropped @khimaros/pi-evolve (Dockerfile + entrypoint); added `hcp` to the persona profile in
        hmux/config.toml (cwd /work via WORKDIR); heartbeat-disable re-homed to EVOLVE_HEARTBEAT_MS read hook-side.
    [x] docs: rename persona-entrypoint.sh -> docker-entrypoint.sh; refresh README + HMUX.md for the hcp-face
        architecture (evolve = the `hcp` face, not a pi extension), current ports (webui 4282 / omni 4284), and
        the native-hub eval path (:4280/ws, not the :4096 face); fixed `make webui` 4092 -> 4282
    [x] purge the retired "evolve" name from current-tense docs/config/comments (HMUX.md, README,
        hmux/config.toml, Dockerfile/entrypoint) in favor of HCP/hmux/persona; keep it only in
        historical/retired notes and the opencode agent marker. dropped the dead EVOLVE_WORKSPACE env
        (Dockerfile + compose; unread -- persona.py resolves from __file__, hmux from cwd) and the dead
        OPENCODE_EVOLVE_WORKSPACE test lookup; removed per.md tools/permission blocks (now the pi
        backend builtins + the permission face in hmux/config.toml). also dropped the redundant
        backend-specific PI_PROJECT_CWD env from the Dockerfile -- hmux already derives it from the
        profile `cwd = "/work"` and sets it on the pi backend itself (node.rs run_pi).
    [~] M2 (pi): validated end-to-end via ../hmux e2e against fake-openai - persona's 7815-char system prompt
        (traits) + all 24 tools register + schema-validate, and a real model tool-call round-trips
        (persona_trait_write -> execute_tool -> file written + face git-commit). full `make eval` against the
        kairos model is model-behavior (orthogonal), run it against the rebuilt image.
    [ ] M3 (opencode): interception (system prompt) works on opencode via the face (backend-agnostic); TOOL
        registration needs route B - an IN-PROCESS http MCP server in the opencode backend (opencode caches
        plugin tools but re-resolves MCP tools per turn). documented in ../hmux backends/opencode/index.ts.
    [x] opencode builtin_tools parity (../hmux): hmux now honors `[profile.x.backend.opencode] builtin_tools`
        like pi's. up.rs backend_env emits HMUX_OPENCODE_BUILTIN_TOOLS; the opencode plugin's `config` hook
        denies the non-whitelisted builtins, which opencode HIDES from the model (resolveTools drops any tool
        whose permission resolves to a global deny -- Permission.disabled). added an inert `backend.opencode`
        table to hmux/config.toml (persona is pi-only, so it only takes effect if opencode joins `backends`).
        needs an hmux rebuild to reach persona's image. tests: units (up.rs backend_env; index.ts
        applyBuiltinWhitelist) + a capability-gated behavior in the BACKEND COMPLIANCE suite
        (tools.builtin_whitelist, gated on a new `builtin_tool_whitelist` manifest capability) that runs
        against pi AND opencode-plugin -- restarts the backend with a one-builtin whitelist, drives a turn,
        asserts the advertised tools keep `read` and hide the rest (serve/attach + acp skip: no in-harness
        glue). folding opencode's restart into compliance needed two harness fixes: run_plugin now reaps its
        child opencode serve on SIGTERM (no orphaned serve; also fixes `hmux up` teardown) and
        restart_backend waits for the old backend to drop before relaunching (opencode's disconnect lags its
        exit). hcp_conform keeps a SLIMMED check (whitelist preserves the hcp face's HOOK tools -- unique,
        since the compliance pi fixture has no hook tools); the standalone opencode test was retired.
    [ ] later (NOT this task): move permissions (pi-permission-system) + bridge (pi-messenger-bridge) into hmux;
        bump *-evolve + airun to HCP v3
[ ] upstream hmux asks (track in ../hmux phase 32): 32a/b/c largely covered by hmux phase 33 (container image + compose); 32d DONE; residuals (make install tarball, hmux up child-restart) only if a non-container deploy is chosen; hub auth (D1)
    [ ] evaluate how the hub/children get config from `hmux up` -- a lot flows via env vars
        (HMUX_PI_BUILTIN_TOOLS, HMUX_OPENCODE_BUILTIN_TOOLS, HMUX_HUB_URL, HMUX_HARNESS_ID,
        HMUX_LISTEN_HOST, HCP_HEARTBEAT_ENABLED, BRIDGE_*), which feels ad hoc. consider handing
        profile-derived config to children explicitly (cli args or a config handoff) instead of env.
[ ] create tools for editing agents/per.md frontmatter
    [ ] permission_add("bash", "cat /etc/*", "ask")
    [ ] permission_remove("bash", "cat /etc/*", "ask")
    [ ] permission_set("webfetch", "deny")
    [~] tools_enable("question")
    [~] tools_disable("patch")
    [~] browser_url_add("https://github.com/khimaros/*", "allow")
    [~] browser_url_remove("https://github.com/khimaros/*", "allow")
    [~] patch("~/.config/opencode/plugins/persona.ts", oldString, newString)
    [~] patch("~/.config/opencode/tools/persona.ts", oldString, newString)
[x] run the whole agent unprivileged (non-root) in the container -- restores chrome's sandbox for
    web browsing and drops the browser-head --no-sandbox hack. verified in-container: sandboxed chrome
    (userns + setuid helper both work), node/opencode/hmux all run as uid 1000.
    [x] hmux base (../hmux): create the `hmux` user (uid/gid 1000, HOME=/data), chown /work + /data,
        add /usr/local/bin/hmux-drop (UNCONDITIONAL full-tree chown of the volumes -> setpriv drop to
        hmux -> exec), point hmux's own ENTRYPOINT at it so hmux runs non-root by default
    [x] persona: docker-entrypoint.sh seeds then `exec hmux-drop "$@"`; browser-head drops the
        `if root -> --no-sandbox` block (--disable-dev-shm-usage stays, it is a container fact not a
        root one); `make browser-head`/`browser-stop` exec `--user hmux` so the shared browser + its
        /data/.chrome-profile stay hmux-owned (docker exec defaults to root)
    [x] HEADED browsing under rootless podman needed keep-id: `hmux` otherwise maps to a SUBUID that
        cannot even connect() the host X socket (0775, owned by you), let alone pass X auth -- the
        agent-as-root path worked before only because rootless maps container-root -> you. fix: compose
        `user: 1000` + `userns_mode: keep-id:uid=${PERSONA_UID:-1000},...` maps hmux -> host you, so a
        non-root (sandboxed) chrome reaches X. hmux-drop made smart: drop-from-root on docker/no-keep-id,
        passthrough when already unprivileged (keep-id). dead ends tried: bare keep-id + keep-id without
        `user:` crash-loop (setpriv-as-root under a broken/odd map, `crun: read pipe failed`); XAUTHORITY
        cookie cannot help since the block is the SOCKET file mode, below X auth
    [x] validate: fresh `make up` runs as uid 1000, no EACCES, hub + pi ready. X11 handshake as hmux =
        CONNECT-OK + AUTHORIZED. `browser-head start` -> HEADED + SANDBOXED (no --no-sandbox) chrome on
        the desktop, browser-use drives it. `make eval FILTER=TestTraitLifecycle` 8/8, `make precommit`
        486/486
[x] split matrix-login out of opencode-operate into its own skill (skills/matrix-login). it
    provisions the hmux BRIDGE face's token, so it was never opencode-specific. it is also
    HOST-side (rewrites the host persona.env, shells out to the hmux checkout) and never worked
    in the container -- .dockerignore now keeps it out of the image and the /usr/local/bin
    symlink is dropped, instead of shipping a skill the agent cannot run
[~] two knobs Per's stack can now turn on, both STAGED COMMENTED in hmux/config.toml (built in
    ../hmux phase 50; see its ROADMAP for the reasoning and the tests)
    [x] dynamic tool dispatch: `dynamic_tools = "only"` on the hcp client puts persona.py's forty-odd
        tools behind hcp_tool_list + hcp_tool_invoke instead of naming every one in every request.
        the permission policy is unaffected -- a dispatched call is judged as the tool it runs, and
        so are the hook's own before_tool/on_permission stages
    [x] runtime permission editing: `tools = true` on the permission client exposes permission_list /
        permission_set / permission_delete, so Per can read this policy and change it for the rest of
        the run (in memory; a restart restores hmux/config.toml). they ask by default and cannot
        rewrite the rule that gates them
    [ ] neither is ON yet. dynamic dispatch changes how every turn is composed, so it wants an eval
        run against the model in use before it decides anything real; the permission tools make every
        policy question a prompt someone has to answer
[ ] fix tool descriptions to prevent trait_delete on .json traits and trait_append on new traits
[x] clarify corrupt .json/.jsonl trait load errors (name the file, point to trait_write) so a bad file is not mistaken for an input-escaping problem
[x] state the JSON-encoding contract in data tool descriptions so the LLM does not over-escape values or misread the read-back
[ ] speech-to-text and text-to-speech for chats
[~] enable default ask and whitelist allow for browser-use
[~] immutable system prompt + one-round-delay notification messages
    [x] trait_changed notices never reach other sessions: the hcp face injected them as a hub
        NOTIFICATION, which the hub dropped (it routes session.inject only as a request), and
        InjectMode::Notice is watcher-only anyway. fixed in ../hmux: InjectMode::NextTurn, queued
        per session and delivered as a system-authored block alongside the next user turn (pi
        role:"custom" message wrapped in <system-reminder>), leaving the system prompt and its
        cache untouched. the face now sends BOTH a notice (watchers see it at edit time) and a
        next_turn block (the agent reads it next turn). no persona-side change: the hook's
        notify + format_notification contract was already right, nothing consumed it
    [ ] still open here: a session's INLINED core traits stay stale for its whole life (the hcp
        prompt cache freezes the composed system prompt per session and never invalidates), so
        the notice is what tells Per to re-read. fine for listed traits, which are read on
        demand; revisit if inlined-trait staleness bites

[x] trait tools: directory cleanup on delete/move, auto-create dirs on write/move
[x] task comments: add comments field, task_comment tool
[x] switch browser-use open --new-tab to separate new-tab and open calls (code and skill)
[x] run pi-webui on 4096 and pi-omni on 4098 (rename pi.service -> pi-omni.service)
[x] allow pushing dev branch of browser-use manually into guest
[x] install forked opencode-ai
[x] record_fields tool: introspect unique fields/values in .jsonl traits
[x] add description field to task data structure
[x] extract opencode-evolve plugin package
[x] move tests into workspace/
[x] heartbeat sessions should occasionally be reset (size based? every turn?)
[x] hook-based plugin architecture (Python hooks, TS shim, IPC via subprocess)
[x] dynamic tool discovery and invocation (discover_tools + invoke_tool)
[x] builtin hook/prompt editing tools with test validation (write/patch_hook, write/patch_prompt)
[x] compaction hook to preserve persona context during session compaction
[x] persist discovered per model to persona-state.json
[x] notify sessions when traits change
[x] add HEARTBEAT mechanism for plugin
[x] send heartbeat messages to LLM in dedicated session
[x] send proactive follow-ups to active per sessions
[x] install forked browser-use
[x] MongoDB-style filters, structured output, {trait:} format, no avatar prefix
[x] add trait edit diffs to debug log
[x] version traits with git and commit after each change
[x] always ask permission for changes to core.md trait
[~] hmux HCP hardening + bridge messaging (this session; changes in ../hmux, ../hcp-spec, ../fake-matrix)
    [x] system prompt REPLACE vs append: hook returns system_mode; persona replaces the backend default
        (was appended after pi's prompt -- the deepseek eval regression). frozen per-session, cache-stable
    [x] compaction interception: hook owns the summarization prompt (compacting stage -> compaction.md),
        falls back to the backend default when no hook. conformance scenario live-tests it (/compact drives it)
    [x] pi builtin-tool whitelist ([profile.persona.backend.pi] builtin_tools=[]) + hcp_hook_edit builtin +
        persona_datetime tool (moved into persona.py). AGENTS.md no longer mentions opencode
    [x] omni + bridge append a face system-prompt note via the mutation chain (omni voice note scoped to its
        own sessions; the bridge already appends room context)
    [x] conformance scenarios added to hcp_conform_test.py (68/0): abstain, compaction, enum-rejection
        (pi TypeBox already rejects invalid enums before execute_tool), permission (on_permission deny), tool-whitelist
    [x] bridge e2e (bridge_test.py 5/0): fixed reply-not-sent -- fake-matrix returned 200 {} for state-event GETs,
        which the sdk read as a redacted m.room.encryption event (send failed EncryptionNotEnabled); now 404 M_NOT_FOUND
    [x] ENCRYPTED-room bridge e2e (enc_bridge_test.py 7/0): fake-matrix e2e RELAY (keys/to-device/encrypted events,
        per-device sync) + a real matrix-sdk helper (fake-matrix/clients/rust). real olm/megolm; no plaintext on the wire
    [x] ROOT-CAUSED the system-prompt regression (traits absent, pi default leaked): omni resolved
        interceptor.resolve as a NOTIFICATION, so the hub (which handles it only as a request) stalled the
        whole mutation chain -> every session reverted to the backend default. fixed omni to use request()
    [x] hub now BOUNDS a mutation chain (MUTATION_CHAIN_TIMEOUT=10s): a face that arms system_prompt but
        never resolves can no longer discard the others' work -- on timeout the hub completes with the
        mutations accumulated so far (hcp's replace preserved). Clock gained schedule/cancel_intercept
    [x] unified client ordering with backend ordering: faces arm with a `kind` (not a self-chosen priority);
        the hub ranks the interception chain by HMUX_CLIENT_PREFERENCES (config order, connect-timing-independent),
        mirroring HMUX_HARNESS_PREFERENCES. removed BRIDGE_PRIORITY/PERMISSION_PRIORITY and the hcp --priority flag
    [x] e2e coverage for the class of bug: multiface_sysprompt_test.py (7/0, hcp+bridge+omni composition) +
        mutation_chain_timeout_test.py (3/0, stalled-face hub bound). full hmux e2e + hub suite green
    [x] bridge-messaging evals GREEN: root cause of the earlier failure was the bridge client sitting COMMENTED
        in hmux/config.toml -- hmux started no bridge face, so bridge_rooms/bridge_send were never registered and
        the model improvised (invented a .matrix_outbox.jsonl trait). uncommented it, rebuilt the persona image,
        re-ran `make eval-bridge MODEL=kairos/muse-glimmer-30b:Q8_0` against the host fake-matrix: 2/2 in 2:22.
        the face connects (@per:fake.local @ :8448 via host.containers.internal), ingests the seeded inbound DM
        (creates a session for !dm:fake.local), and glimmer drives bridge_rooms + bridge_send. real matrix never
        touched -- docker-compose.eval.yml overrides BRIDGE_* with the fake homeserver
    [x] browser-use evals GREEN: `make eval-browser MODEL=kairos/muse-glimmer-30b:Q8_0` (headless real browsing,
        DISPLAY= empty) 4/4 in 8:05 -- start session (browser-head start), navigate news.ycombinator.com,
        visit the top-3 comment threads + summarize each to research_notes.md (>=7 browser-use calls), cleanup.
        confirms baked google-chrome + browser-use + container web egress all work end to end
    [x] Qwen (qwen3.6-27b, REASONING=off) against the rebuilt image. single run: 30/1. REPEAT=3: runs scored
        28/31, 2/31, 27/31. run 2 COLLAPSED (all 29 failures had `actual calls (0)` -- the kairos model endpoint
        stopped emitting tool calls for the whole ~3:40 window; a transient host-side degradation, bracketed by
        healthy runs, NOT a code regression). system-prompt fix confirmed: test_agents_from_system_prompt is the
        sole ALWAYS-PASS (3/3, robust even under the run-2 collapse). the 2 across-x3 always-fails
        (TaskLifecycle::test_04_count_by_status, RecurringTask::test_02_work_on_task) pass in the single run --
        they fail when the model makes an extra reasonable tool call (a 2nd persona_data_count / a cleanup call)
        and assert_calls counts every unmatched call as `required`; the matcher's `also` param (permitted extras)
        is unused in those two tests. test-strictness x model-variance, not a persona bug
    [x] DECISION: leave the 2 strict tests UNCHANGED (user directive -- do not modify tests). the extra-call
        failures stand as model-variance signal; a REPEAT=10 run characterizes true variance instead
    [x] REPEAT=10 Qwen run: per-run 30,30,25,30,30,23,29,26,29,16. aggregate 268/310 (86.5%), 11 always-pass,
        20 flaky, 0 ALWAYS-FAIL (nothing deterministically broken; the x3 "always-fails" were just variance).
        healthy runs sit at 29-30/31. run 10 was another endpoint degradation (13x `actual calls (0)`, truncated
        text) -- test_agents_from_system_prompt failed ONLY there, and only because the reply was cut off after
        "I'm built on:" (the model had begun the correct AGENTS.md self-description -> system prompt WAS composed).
        it is 9/10 = the sole non-degraded-run failure is infra. worst genuine flakies are the 2 strict-matcher
        tests: test_04_count_by_status 2/10 (extra persona_data_count), test_02_work_on_task 4/10 (extra cleanup)
    [ ] endpoint stability: the host-side kairos qwen3.6-27b intermittently degrades (~1-2 runs in 10 emit zero
        tool calls / truncated text for a whole window). not persona/hmux; investigate the model server separately
    [x] deepseek half of the comparison, rebuilt image (REASONING=off, native hub api): deepseek-v4-flash:IQ3_XXS
        30/31 in 14:01 (slowest of the sweep). sole fail = test_agents_from_system_prompt: 2 extra exploratory
        calls (hcp_hook_list + ls /data/.pi/agent/skills) before answering from the inlined AGENTS.md, so the
        strict assert_calls(expect=[]) counts them as required -- test-strictness x model-variance, not a persona
        bug (same class as the qwen extra-call flakies above); the final text answer was correct
    [x] new-model sweep, rebuilt image (REASONING=off, single run each, native hub api): ornith-1.0-35b:Q8_0
        31/31 in 4:29 (perfect + fastest yet), muse-glimmer-30b:Q8_0 (glimmer) 31/31 in 7:50 (perfect). both
        dense 30-35b models clear the qwen3.6-27b default's healthy-run band (29-30/31) with zero single-run fails;
        ornith is the standout (perfect at ~1/3 the deepseek time). strong candidates for a REPEAT=3 sweep to
        challenge the default -- NOT yet promoted (the default's REPEAT-sweep evidence still stands)
[~] hmux: expose faces on the hub port at /c/<path> (single front door) -- upstream feature in ../hmux
    (faces advertise their http addr + kind in the ws hello; the hub reverse-proxies any kind listed in a
    profile-level `expose` map).
    [x] upstream in ../hmux: proto hello advertise, hub /c reverse proxy (http+ws), [expose] config, node
        advertise + subpath-aware webui, e2e/expose_test.py 7/0. NEEDS an hmux image rebuild + publish.
    [x] persona hmux/config.toml: `expose = { webui=/webui, omni=/omni, opencode=/opencode }` staged
        COMMENTED (the older `up` rejects unknown keys; uncomment after the base image is rebuilt). mounts can
        be any path but a hub-reserved one (`/ws`); `up` errors on a reserved/root-less mount.
    [x] bump the base image (rebuild ../hmux + persona), uncomment `expose`, and simplify docker-compose.yml
        to publish only :4280 (drop the per-face 4282/4284/4096 publishes); document reaching faces via /webui etc.
        the per-face lines are kept COMMENTED rather than deleted (uncomment one to reach that face
        directly); the opencode face has no mount, so `make tui` needs its 4096 publish back.
[~] hmux voice face (upstream phase 36 in ../hmux): audio in/out as a first-class modality, so a client
    carries only a mic, a speaker and a local VAD while the face owns stt/tts. the same settings light up
    `/v1/realtime` on the openai face.
    [x] upstream in ../hmux: hmux-voice crate (chunker/sanitize/wav/fence/turn detection/stt+tts mapping),
        FileRef.stream + participants on the protocol, the `voice` face with session-scoped rooms, and
        /v1/realtime. e2e/voice_test.py 9/0 drives a real turn end to end against ../fake-openai's audio endpoints.
    [x] persona.env.example: the HMUX_VOICE_* block, documented as OFF by default.
    [x] enabled here: "voice" in `clients` + `expose` in hmux/config.toml, and the endpoint in
        persona.env. the ordering hazard that made this a two-step is GONE upstream -- an unconfigured
        face now IDLES instead of exiting, so a half-configured profile no longer breaks the container.
    [x] the CLIENT half landed upstream: omni-web and omni-flutter relay to the face and their on-device
        pipelines are deleted.
    [x] config collapsed (2026-08-13): one HMUX_VOICE_URL for both services, falling back to the ambient
        OPENAI_BASE_URL / OPENAI_API_KEY; per-service overrides now cover the KEY as well as the url.
        persona.env's existing HMUX_VOICE_STT_URL keeps working -- it is still the stt-specific override.
    [x] the spoken-reply note moved into the VOICE FACE (upstream 37b) and is scoped to turns the face
        itself drove, via a new `origin` on the interception. persona gets it on every speech path now
        (omni-web, omni-flutter, realtime) instead of only omni-web, and a TYPED turn in the webui on a
        session with a voice publisher attached correctly does NOT get it.

[x] upstream phase 37 (../hmux): the browser clients stop being servers. persona consumes it:
    [x] omni is a STATIC client and its node server is deleted; both browser clients are served by the
        hmux BINARY (axum), so the image ships no client node_modules and node remains only for the pi
        and opencode backends. nothing in persona's config changed for this -- the faces bind the same
        ports and expose the same mounts.
    [x] the SIMPLIFIED chat ui is live at `/chat` (client kind `chat`, port 4290): sessions, a
        transcript, a prompt box. added to `clients` + `expose` in hmux/config.toml. the full webui
        stays at `/webui`; this is the plain surface for reading and typing.

[x] a DEPLOYMENT edits neither docker-compose.yml nor hmux/config.toml (2026-08-21, user-raised).
    both were files a running host had to hand-edit, so each carried host-specific changes AND had
    to absorb every upstream one -- a merge, forever, in a file nobody wants to merge. the fix is to
    make the shipped defaults be what a deployment wants, and give the rest somewhere else to go:
    [x] hmux/config.toml ships the bridge face DECLARED (it idles without HMUX_BRIDGE_*, so this
        costs a deployment that does not want matrix nothing) and heartbeat_cleanup_count = 100.
    [x] persona TRUSTS every configured identity ingress (identity.trust_headers = true, where hmux
        defaults it off), because a persona is a thing PEOPLE talk to and one that cannot tell them
        apart addresses everyone as its single human. this assumes a front door that authenticates
        and strips those headers -- hmux has no auth of its own, so a directly-reachable hub takes
        a forged name from anyone. stated in the file with that assumption written next to it, and
        reversible from persona.env (HMUX_CFG_*, which CREATES a table the file never mentions).
    [x] the one thing left for a deployment to add is a second hook's tool prefix, since a glob key
        cannot go through an env var name. documented commented next to persona_*.
    [x] docker-compose.yml publishes only :4280 and carries a commented host-directory alternative
        to the /work + /data volumes, so the one thing a deployment still changes here is two lines
        it can see, next to the default it is replacing.
    [x] env_file back to the SHORT form for compatibility (the long form needs Compose v2.24+);
        `make up` seeds persona.env from the example, since the short form requires the file.
    [x] found on the way: ports/uid/$DISPLAY were documented in persona.env.example but are compose
        INTERPOLATION, which is resolved BEFORE the container exists and so never reads an env_file
        -- setting them there had never done anything. they are documented where they are read.
    [x] tests/config_defaults_test.py guards the contract: what a deployment must not have to edit,
        and what must stay off in the shipped file.

[x] upstream phase 40 (../hmux, 2026-08-15, user-raised): notifications survive a restart, and a
    proactive dm gets a session. persona consumes both with NO config change:
    [x] the hub now persists its notes (`--state-dir`, derived by `up` as `<state root>/hub`).
        HOME=/data in the container, so the root already lands on the persona-data volume and a
        `make down && make up` no longer drops Per's pending "traits were updated" notices or
        erases the delivered ones from the transcript.
    [x] and each note keeps its message id across a replay, which is the remaining half of the
        user-reported duplicate ("traits were updated: ... " twice, adjacent). the earlier fix
        (2026-08-11) was the hcp face's own doubling; this is the one that survived a reload.
    [x] `bridge_send`/`bridge_send_direct` to someone Per has never talked to now CREATE that
        room's session and record the message into it, so the dm is listed in the webui from the
        moment Per writes, and their reply arrives in the same session with what Per already said
        queued for the turn.

[x] TAXONOMY.md: one map of every concept across the three layers (HCP the protocol, hmux the
    host, persona the agent) -- roles, the normalized data model, every backend and client/face,
    the client library that is becoming the sdk, the hub's mechanisms, the HCP stage tiers,
    persona's own trait/prompt/tool vocabulary, and the configuration surface.
    it ends in TWO indexes, which are the parts that actually cost time when unwritten:
    [x] COLLISION INDEX -- one word, several meanings (client, turn, hook, event, command,
        session, agent, tier, state, request; then a second tier of twenty more).
    [x] SYNONYM INDEX -- several words, one concept. the more expensive half: a collision is
        confusing when you read it, a synonym is invisible until you grep for the wrong one
        and conclude the thing does not exist. fifteen clusters, counted from the source
        (spoke/peer 17+464, backend/harness 560+940, ten verbs for "end something",
        seven for "tell the hub about yourself").

[ ] VOCABULARY CLEANUP, from that inventory. the suggestions below are grouped by who owns
    them; the hmux and hcp-spec ones are staged HERE and raised THERE, the way phase 40 was.

    persona-owned (this repo):
    [ ] HMUX.md says durable face state lives under `/work/.hcp/`. persona sets neither
        `state_dir` nor HMUX_STATE_DIR, so the derived default applies and it is actually
        `$HOME/.local/state/hmux/persona` -- i.e. on the /data volume, not in the workspace.
        the doc is describing the arrangement the state-dir design exists to prevent.
    [ ] "trait" means two things in persona's own tool surface: the `persona_trait_*` FAMILY
        (prose, .md/.txt) and every workspace memory file including the .json and .jsonl ones
        that `persona_data_*` / `persona_record_*` own. the tool descriptions say "trait
        filename in traits/" for all three families, so the word that selects a family is also
        the word for the superset. fix in the parameter descriptions in persona.py (not in
        TOOLS.md -- the descriptions are what the model reads).
    [ ] "prompt" appears three times in one namespace: `persona_prompt_*` edits the mode
        instructions in prompts/, the system prompt is what `mutate_request` composes, and
        `session.prompt` is a user turn. the tools are the only one persona can rename;
        decide whether the churn is worth it before an agent writes a hook that guesses.
    [ ] persona.py has a non-ASCII constant (`AVATAR`), against the ASCII-only style rule.
        harmless today, and worth deciding deliberately rather than by drift.

    upstream ../hmux -- THE SDK NAMES, which are the urgent ones. phase 52i renames
    `hmux-app` to `hmux-client`, and a rename is the cheapest moment to fix a name and the
    most expensive moment to have skipped one:
    [ ] TWO `Client` TYPES IN ONE CRATE. `hmux_app::Client` is the sans-io core and
        `hmux_app::spoke::Client` is the async facade; lib.rs documents why it does not
        re-export the second (which one a caller got would depend on import order). that is a
        workaround for a naming problem, not a fix. after 52i, `hmux_client::Client` should be
        the type the 13 existing call sites already import -- so the core wants a different
        name (`Protocol` / `Core`), and it wants it before the rename, not after.
    [ ] `hmux_app::Command` (a request to send: method + params) collides with
        `hmux_core::Command` (a harness's SLASH command), and both are reachable from one
        `use`. worse, `hmux_app` re-exports the slash-command replica as `command_catalog`,
        so one crate ships both meanings under near-identical names. the builder is a
        `Request`; free `Command` for the wire type it already belongs to.
    [ ] `Event` means three things in the client alone: `SessionEvent` (the wire),
        `hmux_app::Event` (an inbound frame worth waking for), and `take_events()` (raw
        relayed notifications). name the middle one for what it is (`Wake` / `Inbound`).
    [ ] `Observer::state()` reads the hub's opaque KV store, `link()` reads the connection
        state, and neither is `ConditionState`, `SessionStatus`, or the state DIR. the KV
        accessors want a name that says store (`kv` / `shared`).
    [ ] "session" is both a conversation and an SSO cookie -- `insecure_session`,
        `is_session_cookie`, `Dial.cookie` ("the portal session"). in the crate whose whole
        subject is sessions, the credential should say credential.
    [ ] FOURTEEN METHODS ARE FORWARDED VERBATIM through Connection -> Feed -> Observer ->
        Client, and `native.rs` and `web.rs` each re-declare all of them by hand. adding a
        fifteenth means four edits in two files that must not diverge -- and the two
        transports diverging silently is exactly the failure `feed.rs` was factored out to
        prevent. the five-call FFI boundary is the shape that should be the only surface.
    [ ] one verb for draining: `drain()`, `take_events()`, `take_control()`,
        `take_tool_calls()` are four names for one operation over four payloads. likewise
        `dropped()` vs `events_dropped()` -- two counters, near-identical names, different
        subjects; name the subject in both.
    [ ] `hmux_app::Observer` collides with `hrns_core::Observer` (the agent loop's event
        sink), which hmux consumes in-process. the crate already has `Watched`; `Watcher` is
        free and self-consistent.
    [ ] `reset` means three things: `SessionEvent::Reset` (history was replaced),
        `Transcript::reset(history)` (seed from a snapshot), `Client::reset()` (forget the
        handshake). the middle one is `seed`.
    [ ] `crates/hmux-client/src/lib.rs` says "52h finishes it the other way round"; the
        roadmap item is 52i (52h is typescript-onto-the-core). one of the two is wrong and
        the shim is the file a reader lands on first.

    upstream ../hmux -- the wider vocabulary, cheaper and less urgent:
    [ ] backend vs harness: the wire says `harness.*` and `harness_id`, the CLI says `hmux
        backend`, and `Session` carries `harness_id` AND `backend_id` in one struct meaning
        two different things (the owner, and the owner's own id for this session). state the
        rule once in DESIGN, and rename `backend_id` to name what it is (`native_id`).
    [ ] spoke vs peer: DESIGN says spoke (17), the code says peer (464), and `hmux_app::Peer`
        is a third thing again (what to declare at hello). pick one word for "a process
        connected to the hub" and let `Peer` keep the sdk meaning.
    [ ] ten verbs end something -- abort, cancel, stop, dispose, close, release, reap, delete,
        veto, deny. four are distinct wire methods and the rest are prose. one table in DESIGN
        naming which is which, and prose that uses those four words for nothing else.
    [ ] seven verbs tell the hub something durable about yourself: register, arm, declare,
        set, subscribe, advertise, announce. this one is probably not worth renaming methods
        over, but it is worth saying in DESIGN which shape they share.
    [ ] finish the `Condition` consolidation. its own doc says it replaced six ways of telling
        a human something; two of the six (`ui.notify` and `SessionEvent::Notice`) still exist
        beside it, so there are three live mechanisms and a client must render all three.
    [ ] conformance vs compliance: hcp-spec has CONFORMANCE.md, hmux has a `hmux-conform`
        crate AND calls the opencode face's suite "compliance". same activity, two words.

    upstream ../hcp-spec:
    [ ] "stage" vs "hook". the prose says stage 17 times and hook 35 times for the same thing,
        and the base payload field is `hook` carrying a stage name -- while hmux uses "hook"
        for an interception point and `hcp_hook_write` uses it for the SCRIPT FILE. four
        meanings, one of them normative. renaming the payload field to `stage` is a v4 change;
        making the prose consistent is not, and is most of the win.
    [ ] the six scalar response keys carry six different things by stage, and two of them mean
        the SAME thing: `user` (heartbeat, recover) and `continue` (before_stop) are both "a
        synthetic user turn". meanwhile `prompt` in `compacting` is summarization
        instructions, not a prompt in any other sense, and `result` is a tool return value, a
        replacement result, AND synthetic assistant text depending on where it appears.
        TAXONOMY.md now carries the table; the spec should carry it too.
    [ ] HCP's tiers (0 universal / 1 loop-aware / 2 interception / 3 host extensions) and
        hmux's tiers (1 hub-boundary / 2 in-harness mutation) are unrelated numbering schemes
        that meet in every document about this stack. one of them should stop being called a
        tier.
```
