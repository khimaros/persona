#!/bin/bash

set -xeuo pipefail

export PATH="${HOME}/.local/bin:${PATH}"

npm config set prefix ~/.local

# install opencode
#nix --extra-experimental-features nix-command --extra-experimental-features flakes profile add github:khimaros/opencode
#npm -g install "opencode-ai@v1.2.26"
#npm -g install "opencode-ai@latest"
[[ -d "opencode" ]] || git clone --recurse-submodules -b dev https://github.com/khimaros/opencode
pushd opencode
git pull
npm -g install bun
bun install
./packages/opencode/script/build.ts --single
cp ./packages/opencode/dist/opencode-linux-x64/bin/opencode ~/.local/bin/
popd

# install opencode plugins
pushd ~/.config/opencode
npm install --legacy-peer-deps github:khimaros/opencode-evolve github:khimaros/opencode-bridge
popd

# install uv
#cargo install --locked uv
which uv &>/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

# install browser-use
#uv tool install -U browser-use[cli]
uv tool install -U git+https://github.com/khimaros/browser-use[cli]

# initialize git repo: required to avoid "global" project in "/"
pushd ~/workspace/
[[ -d .git ]] || git init
#git add .
#git commit -m 'initial import'
popd

systemctl --user daemon-reload

systemctl --user enable opencode.service

# FIXME: browser-use sometimes produces zombies that hang opencode shutdown
# presumably only if session is started by opencode bash?
pgrep -f browser-use && pkill -9 -f browser-use

systemctl --user restart opencode.service

# remove "global" project in "/" from project list
#sqlite3 ~/.local/state/opencode/opencode.db 'DELETE FROM project WHERE id = "global";'

# remove this script
rm -f "$0"
