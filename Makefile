test:
	python3 server/workspace/tests/persona_test.py
.PHONY: test

precommit: test
.PHONY: precommit
