#!/bin/bash

set -xeuo pipefail

npm config set prefix ~/.local
cd ~/.config/opencode && npm install --legacy-peer-deps github:khimaros/opencode-evolve && cd -

type ~/.local/bin/uv &>/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
#cargo install --locked uv

~/.local/bin/uv tool install git+https://github.com/khimaros/browser-use[cli]

systemctl --user daemon-reload

systemctl --user enable opencode.service

# FIXME: browser-use sometimes produces zombies that hang opencode shutdown
# presumably only if session is started by opencode bash?
pgrep -f browser-use && pkill -9 -f browser-use

systemctl --user restart opencode.service

rm -f "$0"
