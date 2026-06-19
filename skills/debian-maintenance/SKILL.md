---
name: debian-maintenance
description: Comprehensive skill for Debian/Ubuntu system maintenance including upgrades, package management, and cleanup.
compatibility: Debian-based systems (Ubuntu, Mint, etc.) with apt package manager.
---

# Debian System Maintenance Skill

Use this skill to perform routine maintenance on Debian-based systems.

## 🚀 System Upgrades

Perform full system upgrades with the following default behavior:
- `apt-get update` to refresh package lists.
- `apt-get full-upgrade -y` to upgrade all packages.
- `--autoremove` and `--purge` to clean up unused dependencies and configuration files.
- **Changelog Summary**: After upgrading, fetch and summarize the changelog for the updated packages using `apt-get changelog <package>`.

**Standard Upgrade Command:**
```bash
sudo apt-get update && sudo apt-get full-upgrade -y --autoremove --purge
```

**Fetch Changelog:**
```bash
apt-get changelog <package>
```

## 📦 Package Management

### Discovery
- **Search**: `apt-cache search <query>`
- **Show details**: `apt-cache show <package>`
- **Check installed**: `dpkg -l | grep <query>`
- **List upgradable**: `apt list --upgradable`

### Installation
- **Install**: `sudo apt-get install -y <package>`
- **Reinstall**: `sudo apt-get install --reinstall <package>`
- **Build-deps**: `sudo apt-get build-dep <package>`

### Removal
- **Remove**: `sudo apt-get remove <package>`
- **Purge**: `sudo apt-get purge <package>` (removes configuration files too)
- **Auto-remove**: `sudo apt-get autoremove` (removes unused dependencies)

## 🧹 Cleanup
- **Clean cache**: `sudo apt-get clean` (removes all downloaded .deb files)
- **Auto-clean**: `sudo apt-get autoclean` (removes only obsolete .deb files)
- **Purge configuration**: `sudo apt-get purge $(dpkg -l | grep "^rc" | awk '{print $2}')` (removes configs of uninstalled packages)

## 🔍 Diagnostics
- **Check**: `sudo apt-get check` (verifies package database)
- **Fix Broken**: `sudo apt-get install -f` (attempts to fix broken dependencies)
- **Configure Pending**: `sudo dpkg --configure -a` (configures unpacked but unconfigured packages)

---

## 📚 Further Reading
For advanced operations including source list management and PPA handling, see the [Advanced Reference](references/REFERENCE.md).
