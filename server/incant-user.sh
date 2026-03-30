#!/bin/bash

set -xeuo pipefail

export PATH="${HOME}/.local/bin:${PATH}"

npm config set prefix ~/.local

# install opencode
#nix --extra-experimental-features nix-command --extra-experimental-features flakes profile add github:khimaros/opencode
#npm -g install "opencode-ai@v1.2.26"
#npm -g install "opencode-ai@latest"
OPENCODE_NEEDS_BUILD=false
if [[ ! -d "opencode" ]]; then
  git clone --recurse-submodules -b dev https://github.com/khimaros/opencode
  OPENCODE_NEEDS_BUILD=true
fi
pushd opencode
git fetch origin
OLD_HEAD=$(git rev-parse HEAD)
git reset --hard origin/dev
NEW_HEAD=$(git rev-parse HEAD)
if [[ "$OLD_HEAD" != "$NEW_HEAD" ]]; then
  OPENCODE_NEEDS_BUILD=true
fi
if [[ "$OPENCODE_NEEDS_BUILD" == "true" ]]; then
  npm -g install bun
  bun install
  ./packages/opencode/script/build.ts --single
  systemctl --user stop opencode.service || true
  cp ./packages/opencode/dist/opencode-linux-x64/bin/opencode ~/.local/bin/
fi
popd

# install opencode plugins, using local @opencode-ai/plugin from the build
# to avoid fetching the unpublished dev version from npm.
#
# opencode's needsInstall() skips bun install when:
#   1. node_modules/@opencode-ai/plugin exists
#   2. package.json has @opencode-ai/plugin == Installation.VERSION
# so we pre-seed both to prevent the startup install from hitting npm.
OPENCODE_VERSION=$(opencode --version)

# strip @opencode-ai/plugin from package.json so npm install doesn't try to fetch it
for dir in ~/.config/opencode ~/.opencode; do
  [[ -f "$dir/package.json" ]] && node -e "
    const fs = require('fs');
    const f = '$dir/package.json';
    const p = JSON.parse(fs.readFileSync(f));
    delete (p.dependencies || {})['@opencode-ai/plugin'];
    fs.writeFileSync(f, JSON.stringify(p, null, 2));
  "
done

pushd ~/.config/opencode
#npm install -U --legacy-peer-deps github:khimaros/opencode-evolve github:khimaros/opencode-bridge
npm install -U github:khimaros/opencode-evolve github:khimaros/opencode-bridge
popd

# copy local @opencode-ai/plugin and stamp package.json with the dev version
# so opencode's needsInstall() is satisfied at startup
seed_plugin() {
  local dir="$1"
  mkdir -p "$dir/node_modules/@opencode-ai"
  for pkg in plugin sdk; do
    rm -rf "$dir/node_modules/@opencode-ai/$pkg"
    cp -rL ~/opencode/node_modules/@opencode-ai/$pkg "$dir/node_modules/@opencode-ai/$pkg"
  done
  node -e "
    const fs = require('fs');
    const mf = '$dir/node_modules/@opencode-ai/plugin/package.json';
    const m = JSON.parse(fs.readFileSync(mf));
    m.version = '$OPENCODE_VERSION';
    fs.writeFileSync(mf, JSON.stringify(m, null, 2));
    const pf = '$dir/package.json';
    const p = fs.existsSync(pf) ? JSON.parse(fs.readFileSync(pf)) : {};
    p.dependencies = p.dependencies || {};
    p.dependencies['@opencode-ai/plugin'] = '$OPENCODE_VERSION';
    fs.writeFileSync(pf, JSON.stringify(p, null, 2));
  "
}
seed_plugin ~/.config/opencode
seed_plugin ~/.opencode

# install uv
#cargo install --locked uv
which uv &>/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

# install browser-use
#uv tool install -U browser-use[cli]
uv tool install -U git+https://github.com/khimaros/browser-use[cli]

# symlink opencode skill scripts into PATH
ln -sf ~/.config/opencode/skills/browser-use/scripts/browser-head ~/.local/bin/
ln -sf ~/.config/opencode/skills/opencode-operate/scripts/matrix-login ~/.local/bin/
ln -sf ~/.config/opencode/skills/opencode-operate/scripts/update-opencode-models ~/.local/bin/

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
#pgrep -f browser-use && pkill -9 -f browser-use

systemctl --user restart opencode.service

# remove "global" project in "/" from project list
#sqlite3 ~/.local/state/opencode/opencode.db 'DELETE FROM project WHERE id = "global";'

# remove this script
rm -f "$0"
