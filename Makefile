include server/incant.env

INSTANCE?="persona-eval"

#MODEL?="custom/gemma-4-26b-a4b-it:Q8_0"
#MODEL?="custom/minimax-m2.7:UD-IQ4_XS"
#MODEL?="custom/qwen3.5-27b:Q8_0"
#MODEL?="custom/qwen3.5-35b-a3b:Q8_0"
#MODEL?="custom/qwen3.6-27b:Q8_0"
MODEL?="custom/qwen3.6-35b-a3b:Q8_0"

FILTER?=
REPEAT?=1

ADDRESS=$(shell incus list -f json $(INSTANCE) | jq -r '.[0].state.network.eth0.addresses[] | select(.family == "inet") .address' | head -n 1)

test:
	python3 server/workspace/tests/persona_test.py
.PHONY: test

eval:
	make INSTANCE=$(INSTANCE) -C server/ reset-workspace disable-heartbeat service-restart && sleep 5
	rm -f /tmp/eval-run-*.xml
	for i in $$(seq 1 $(REPEAT)); do \
		echo "=== eval run $$i/$(REPEAT) ==="; \
		OPENCODE_URL=http://$(ADDRESS):4096 OPENCODE_DIR=$(USER_HOME)/workspace OPENCODE_MODEL=$(MODEL) pytest evals/persona_eval.py -v -W all $(if $(FILTER),-k "$(FILTER)") --junitxml=/tmp/eval-run-$$i.xml || true; \
	done
	@if [ $(REPEAT) -gt 1 ]; then python3 evals/aggregate.py /tmp/eval-run-*.xml; fi
.PHONY: eval

precommit: test
.PHONY: precommit
