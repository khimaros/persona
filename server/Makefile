include incant.env

INSTANCE?="persona"

API_HOST?="localhost"

API_BASE?="http://$(API_HOST):7860/v1"

ADDRESS=$(shell incus list -f json $(INSTANCE) | jq -r '.[0].state.network.eth0.addresses[] | select(.family == "inet") .address' | head -n 1)

default:
.PHONY: default

create: up push-workspace provision-user
.PHONY: create

update: provision provision-user
.PHONY: update

bootstrap:
	sudo apt install incant jq
.PHONY: bootstrap

up:
	incant up $(INSTANCE)
.PHONY: up

provision:
	incant provision $(INSTANCE)
.PHONY: provision

provision-user: push-ssh-pubkeys
	incus file push --mode 0755 --uid 1000 --gid 1000 ./incant-user.sh $(INSTANCE)/$(USER_HOME)/incant-user.sh
	ssh $(USER_NAME)@$(ADDRESS) -- ./incant-user.sh
.PHONY: provision-user

clear-opencode:
	ssh $(USER_NAME)@$(ADDRESS) -- rm -rf $(USER_HOME)/.config/opencode/
.PHONY: clear-opencode

reset-workspace: clear-workspace push-workspace
.PHONY: reset-workspace

clear-workspace:
	ssh $(USER_NAME)@$(ADDRESS) -- rm -rf $(USER_HOME)/workspace/
.PHONY: clear-workspace

push: update push-opencode-providers push-workspace-config push-workspace-tests service-restart
.PHONY: push

push-workspace:
	incus file push --recursive ./workspace/ $(INSTANCE)/$(USER_HOME)/
.PHONY: push-workspace

push-workspace-traits-base:
	incus file push ./workspace/traits/AGENTS.md $(INSTANCE)/$(USER_HOME)/workspace/traits/
	incus file push ./workspace/traits/TOOLS.md $(INSTANCE)/$(USER_HOME)/workspace/traits/
	incus file push ./workspace/traits/HEARTBEAT.md $(INSTANCE)/$(USER_HOME)/workspace/traits/
.PHONY: push-workspace-traits-base

push-workspace-tests:
	incus file push --recursive ./workspace/tests/ $(INSTANCE)/$(USER_HOME)/workspace/
.PHONY: push-workspace-tests

push-workspace-hooks:
	incus file push --recursive ./workspace/hooks/ $(INSTANCE)/$(USER_HOME)/workspace/
.PHONY: push-workspace-hooks

push-workspace-config:
	incus file push --recursive ./workspace/config/evolve.jsonc $(INSTANCE)/$(USER_HOME)/workspace/config/
	incus file push --recursive ./workspace/config/bridge.jsonc $(INSTANCE)/$(USER_HOME)/workspace/config/
.PHONY: push-workspace-config

push-ssh-pubkeys:
	cat $(HOME)/.ssh/*.pub > /tmp/incant-authorized_keys
	incus file push --create-dirs --mode 0600 --uid 1000 --gid 1000 /tmp/incant-authorized_keys $(INSTANCE)/$(USER_HOME)/.ssh/authorized_keys; rm /tmp/incant-authorized_keys
	ssh $(USER_NAME)@$(ADDRESS) -o StrictHostKeyChecking=accept-new -- true
.PHONY: push-ssh-pubkeys

push-opencode-auth:
	incus file push --create-dirs --mode 0600 --uid 1000 --gid 1000 $(HOME)/.local/share/opencode/auth.json $(INSTANCE)/$(USER_HOME)/.local/share/opencode/
.PHONY: push-opencode-auth

push-opencode-providers:
	ssh $(USER_NAME)@$(ADDRESS) -- $(USER_HOME)/.local/bin/update-opencode-models --api-base $(API_BASE)
.PHONY: push-opencode-providers

cleanup-browsers:
	ssh $(USER_NAME)@$(ADDRESS) -- pkill -9 -f browser
.PHONY: cleanup-browsers

service-restart:
	ssh $(USER_NAME)@$(ADDRESS) -- systemctl --user restart opencode.service
.PHONY: service-restart

ssh:
	ssh -o ForwardX11=yes -t $(USER_NAME)@$(ADDRESS) -- screen -x -R -S control
.PHONY: ssh

browser-head:
	clear && ssh -o ForwardX11=yes -t $(USER_NAME)@$(ADDRESS) -- "$(USER_HOME)/.local/bin/browser-head start && $(USER_HOME)/.local/bin/browser-head wait"
.PHONY: browser-head

browser-stop:
	ssh -t $(USER_NAME)@$(ADDRESS) -- $(USER_HOME)/.local/bin/browser-head stop
.PHONY: browser-stop

eval:
	OPENCODE_URL=http://$(ADDRESS):4096 OPENCODE_DIR=$(USER_HOME)/workspace pytest tests/persona_eval.py -v
.PHONY: eval

webui:
	open http://$(ADDRESS):4096/
.PHONY: webui

tui:
	opencode attach http://$(ADDRESS):4096
.PHONY: tui

start:
	incus start $(INSTANCE)
.PHONY: start

stop:
	incus stop $(INSTANCE)
.PHONY: stop

restart:
	incus restart $(INSTANCE)
.PHONY: restart

destroy:
	incant destroy $(INSTANCE)
.PHONY: destroy

forward-port:
	incus config device add $(INSTANCE) port-4096 proxy listen=tcp:0.0.0.0:4096 connect=tcp:127.0.0.1:4096
.PHONY: forward-port

console:
	@clear
	@ssh -t $(USER_NAME)@$(ADDRESS) -- "journalctl --user -u opencode -o cat -g '^\\[(evolve|bridge)\\]:' -f"
.PHONY: console

console-opencode:
	@clear
	@ssh -t $(USER_NAME)@$(ADDRESS) -- journalctl --user -u opencode -o cat -f
.PHONY: console-opencode

console-evolve:
	@clear
	@ssh -t $(USER_NAME)@$(ADDRESS) -- journalctl --user -u opencode -o cat -g '^\\[evolve\\]:' -f
.PHONY: console-evolve

console-persona:
	@clear
	@ssh -t $(USER_NAME)@$(ADDRESS) -- journalctl --user -u opencode -o cat -g '^\\[evolve\\]:\ \\[🌀\\]' -f
.PHONY: console-persona

console-bridge:
	@clear
	@ssh -t $(USER_NAME)@$(ADDRESS) -- journalctl --user -u opencode -o cat -g '^\\[bridge\\]:' -f
.PHONY: console-bridge

console-startup:
	@clear
	@ssh -t $(USER_NAME)@$(ADDRESS) -- "journalctl --user -u opencode -o cat -g '(plugin|bun|install|loading)' -f"
.PHONY: console-startup
