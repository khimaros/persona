#!/bin/bash

set -xeuo pipefail

export PATH="${HOME}/.local/bin:${PATH}"

OPENCODE_DIRS=(~/.config/opencode ~/.opencode)
PLUGIN_NAMES=(opencode-evolve opencode-bridge)

PLUGIN_BUNDLE=~/opencode/packages/plugin/dist/index.js
SDK_BUNDLE=~/opencode/packages/sdk/js/dist/client.js

# satisfy opencode's auto-install (Npm.install in packages/opencode/src/npm/index.ts)
# by writing a package.json + lockfile pair where every declared dep is also
# locked, so the "in sync" fast path runs and reify never fires.
write_manifest() {
  local dir="$1" version="$2"
  node -e "
    const fs = require('fs');
    const v = '$version';
    const deps = { '@opencode-ai/plugin': v, '@opencode-ai/sdk': v };
    fs.writeFileSync('$dir/package.json',
      JSON.stringify({ name: 'opencode-config', version: '1.0.0', dependencies: deps }, null, 2));
    fs.writeFileSync('$dir/package-lock.json', JSON.stringify({
      name: 'opencode-config', version: '1.0.0', lockfileVersion: 3, requires: true,
      packages: { '': { name: 'opencode-config', version: '1.0.0', dependencies: deps } },
    }, null, 2));
  "
}

# write a minimal node_modules/<scope>/<name>/ shim from a single bundle file.
# extra is a JSON fragment appended to the package.json (e.g. an exports map).
shim_pkg() {
  local dir="$1" scope="$2" name="$3" src="$4" version="$5" extra="${6:-}"
  local out="$dir/node_modules/$scope/$name"
  mkdir -p "$out"
  cp -L "$src" "$out/index.js"
  printf '{"name":"%s/%s","version":"%s","type":"module","main":"index.js"%s}\n' \
    "$scope" "$name" "$version" "$extra" > "$out/package.json"
}

# populate ~/.config/opencode and ~/.opencode with the @opencode-ai shims and
# the synthetic manifest. plugins themselves load TS-natively from their own
# node_modules/<plugin>/src/index.ts via bun's runtime.
seed_plugins() {
  local version
  version=$(opencode --version)
  for dir in "${OPENCODE_DIRS[@]}"; do
    [[ -d "$dir" ]] || continue
    mkdir -p "$dir/node_modules"
    shim_pkg "$dir" @opencode-ai plugin "$PLUGIN_BUNDLE" "$version"
    shim_pkg "$dir" @opencode-ai sdk    "$SDK_BUNDLE"    "$version" \
      ',"exports":{"./client":"./index.js"}'
    write_manifest "$dir" "$version"
  done
}

# place a plugin source tree (with TS source) into every opencode dir.
# bun loads the .ts files at runtime; no compilation needed.
place_plugin() {
  local src="$1" name="$2"
  for dir in "${OPENCODE_DIRS[@]}"; do
    [[ -d "$dir" ]] || continue
    mkdir -p "$dir/node_modules"
    rm -rf "$dir/node_modules/$name"
    cp -rL "$src" "$dir/node_modules/$name"
  done
}

install_opencode() {
  if [[ ! -d "opencode" ]]; then
    git clone --recurse-submodules -b dev https://github.com/khimaros/opencode
  fi
  pushd opencode
  git fetch origin
  git reset --hard origin/dev
  build_opencode
  popd
}

install_opencode_dev() {
  pushd ~/opencode
  build_opencode
  popd
}

build_opencode() {
  npm -g install bun
  bun install
  OPENCODE_CHANNEL=dev ./packages/opencode/script/build.ts --single
  systemctl --user stop opencode.service || true
  cp ./packages/opencode/dist/opencode-linux-x64/bin/opencode ~/.local/bin/

  # bundle @opencode-ai/plugin and @opencode-ai/sdk client into single .js
  # files so plugins can `import { tool } from "@opencode-ai/plugin"` without
  # us having to copy and patch the upstream source tree (which uses
  # `./tool.js`-style imports that bun's runtime won't rewrite).
  bun build packages/plugin/src/index.ts \
    --outfile packages/plugin/dist/index.js \
    --target node --format esm
  bun build packages/sdk/js/src/client.ts \
    --outfile packages/sdk/js/dist/client.js \
    --target node --format esm
}

install_plugins() {
  local tmp; tmp=$(mktemp -d)
  for name in "${PLUGIN_NAMES[@]}"; do
    git clone --depth=1 https://github.com/khimaros/"$name" "$tmp/$name"
    place_plugin "$tmp/$name" "$name"
  done
  rm -rf "$tmp"
  seed_plugins
}

install_plugins_dev() {
  local tmp; tmp=$(mktemp -d)
  for name in "${PLUGIN_NAMES[@]}"; do
    local tgz
    tgz=$(ls /tmp/"$name"-*.tgz 2>/dev/null | head -1) || true
    [[ -n "$tgz" && -f "$tgz" ]] || continue
    rm -rf "$tmp/extract" && mkdir -p "$tmp/extract"
    tar -xzf "$tgz" -C "$tmp/extract"
    place_plugin "$tmp/extract/package" "$name"
  done
  rm -rf "$tmp" /tmp/opencode-evolve-*.tgz /tmp/opencode-bridge-*.tgz
  seed_plugins
}

install_browser_use() {
  which uv &>/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  uv tool install --force -U git+https://github.com/khimaros/browser-use[cli]
}

install_browser_use_dev() {
  which uv &>/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  uv tool install --force -U ~/browser-use[cli]
}

main() {
  npm config set prefix ~/.local

  if [[ "${DEV:-}" == "1" ]]; then
    install_opencode_dev
    install_plugins_dev
    install_browser_use_dev
  else
    install_opencode
    install_plugins
    install_browser_use
  fi

  # symlink opencode skill scripts into PATH
  ln -sf ~/.config/opencode/skills/browser-use/scripts/browser-head ~/.local/bin/
  ln -sf ~/.config/opencode/skills/opencode-operate/scripts/matrix-login ~/.local/bin/
  ln -sf ~/.config/opencode/skills/opencode-operate/scripts/update-opencode-models ~/.local/bin/

  # initialize git repo: required to avoid "global" project in "/"
  pushd ~/workspace/
  [[ -d .git ]] || git init
  popd

  systemctl --user daemon-reload
  systemctl --user enable opencode.service
  systemctl --user restart opencode.service

  # remove this script
  rm -f "$0"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
