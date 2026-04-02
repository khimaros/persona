include server/incant.env

INSTANCE?="persona-eval"

ADDRESS=$(shell incus list -f json $(INSTANCE) | jq -r '.[0].state.network.eth0.addresses[] | select(.family == "inet") .address' | head -n 1)

test:
	python3 server/workspace/tests/persona_test.py
.PHONY: test

eval:
	make INSTANCE=$(INSTANCE) -C server/ reset-workspace service-restart && sleep 5
	OPENCODE_URL=http://$(ADDRESS):4096 OPENCODE_DIR=$(USER_HOME)/workspace OPENCODE_MODEL=openai-compatible/qwen3.5-27b:Q8_0 pytest evals/persona_eval.py -v -W all
.PHONY: eval

precommit: test
.PHONY: precommit
