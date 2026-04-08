#!/bin/bash

set -xeuo pipefail

export PATH="${HOME}/.local/bin:${PATH}"

OPENCODE_DIRS=(~/.config/opencode ~/.opencode)
PLUGIN_NAMES=(opencode-evolve opencode-bridge)

OPENCODE_SRC=~/opencode
PLUGIN_BUNDLE=$OPENCODE_SRC/packages/plugin/dist/index.js
SDK_BUNDLE=$OPENCODE_SRC/packages/sdk/js/dist/client.js

DEV=${DEV:-0}
if [[ "$DEV" == "1" ]]; then
  BROWSER_USE_SRC=~/browser-use
else
  BROWSER_USE_SRC='git+https://github.com/khimaros/browser-use'
fi

# satisfy opencode's auto-install (Npm.install in packages/opencode/src/npm/index.ts)
# by writing a package.json + lockfile pair where every declared dep is also
# locked, so the "in sync" fast path runs and reify never fires.
write_manifest() {
  local dir="$1" version="$2"
  cat > "$dir/package.json" <<EOF
{
  "name": "opencode-config",
  "version": "1.0.0",
  "dependencies": {
    "@opencode-ai/plugin": "$version",
    "@opencode-ai/sdk": "$version"
  }
}
EOF
  cat > "$dir/package-lock.json" <<EOF
{
  "name": "opencode-config",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "dependencies": {
        "@opencode-ai/plugin": "$version",
        "@opencode-ai/sdk": "$version"
      }
    }
  }
}
EOF
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
    shim_pkg "$dir" @opencode-ai plugin "$PLUGIN_BUNDLE" "$version"
    shim_pkg "$dir" @opencode-ai sdk    "$SDK_BUNDLE"    "$version" \
      ',"exports":{"./client":"./index.js"}'
    write_manifest "$dir" "$version"
  done
}

# place a plugin source tree into every opencode dir's node_modules.
# bun loads the .ts files at runtime; no compilation needed.
place_plugin() {
  local src="$1" name="$2"
  for dir in "${OPENCODE_DIRS[@]}"; do
    [[ -d "$dir" ]] || continue
    rm -rf "$dir/node_modules/$name"
    mkdir -p "$dir/node_modules"
    cp -rL "$src" "$dir/node_modules/$name"
  done
}

install_opencode() {
  # in DEV the source tree is rsynced in by push-sources-dev; otherwise clone.
  if [[ "$DEV" != "1" ]]; then
    if [[ ! -d "$OPENCODE_SRC" ]]; then
      git clone --recurse-submodules -b dev https://github.com/khimaros/opencode "$OPENCODE_SRC"
    fi
    git -C "$OPENCODE_SRC" fetch origin
    git -C "$OPENCODE_SRC" reset --hard origin/dev
  fi
  pushd "$OPENCODE_SRC"
  npm -g install bun
  bun install
  OPENCODE_CHANNEL=dev ./packages/opencode/script/build.ts --single
  systemctl --user stop opencode.service || true
  cp ./packages/opencode/dist/opencode-linux-x64/bin/opencode ~/.local/bin/

  # bundle @opencode-ai/{plugin,sdk} into self-contained .js files. upstream
  # source uses `./tool.js` style imports that bun's runtime won't rewrite,
  # so we resolve them at build time when bun is TS-aware.
  bun build packages/plugin/src/index.ts \
    --outfile "$PLUGIN_BUNDLE" --target node --format esm
  bun build packages/sdk/js/src/client.ts \
    --outfile "$SDK_BUNDLE" --target node --format esm
  popd
}

# fetch a plugin source tree into $dest. in DEV reads the tarball pushed to
# /tmp by push-sources-dev; otherwise clones the github repo.
fetch_plugin() {
  local dest="$1" name="$2"
  mkdir -p "$dest"
  if [[ "$DEV" == "1" ]]; then
    local tgz=/tmp/"$name"-*.tgz
    tar -xzf $tgz -C "$dest" --strip-components=1
  else
    git clone --depth=1 https://github.com/khimaros/"$name" "$dest"
  fi
}

install_plugins() {
  local tmp; tmp=$(mktemp -d)
  for name in "${PLUGIN_NAMES[@]}"; do
    fetch_plugin "$tmp/$name" "$name"
    place_plugin "$tmp/$name" "$name"
  done
  rm -rf "$tmp" /tmp/opencode-evolve-*.tgz /tmp/opencode-bridge-*.tgz
  seed_plugins
}

install_browser_use() {
  which uv &>/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  uv tool install --force -U "$BROWSER_USE_SRC"'[cli]'
}

main() {
  npm config set prefix ~/.local

  install_opencode
  install_plugins
  install_browser_use

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
