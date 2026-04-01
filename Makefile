include server/incant.env

INSTANCE?="persona"

ADDRESS=$(shell incus list -f json $(INSTANCE) | jq -r '.[0].state.network.eth0.addresses[] | select(.family == "inet") .address' | head -n 1)

test:
	python3 server/workspace/tests/persona_test.py
.PHONY: test

eval:
	OPENCODE_URL=http://$(ADDRESS):4096 OPENCODE_DIR=$(USER_HOME)/workspace pytest evals/persona_eval.py -v
.PHONY: eval

precommit: test
.PHONY: precommit
