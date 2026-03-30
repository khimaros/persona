---
name: opencode-operate
description: Operate OpenCode, manage agents, create/update skills, and develop plugins following official specifications and best practices.
metadata:
  version: "4.1.1"
  author: Maint
---

# OpenCode Operation Skill

Use this skill to manage and extend OpenCode's capabilities, including configuration, skills, plugins, and custom tools.

## When to use this skill

Use this skill when you need to:
- Modify OpenCode configuration files (`opencode.json`).
- Create, edit, or update **Agent Skills** following the [Agent Skills specification](https://agentskills.io/specification).
- Develop and manage **OpenCode Plugins** and **Custom Tools**.
- Configure and manage specialized agents.
- Manage the OpenCode systemd service.
- Synchronize models from external providers.

---

## 🛠️ Agent Skills

Skills must be organized in directories containing a `SKILL.md` file with valid YAML frontmatter.

- **Standard**: Follow the [Agent Skills Reference](references/opencode-skills.md).
- **Placement**: `.opencode/skills/` (project) or `~/.config/opencode/skills/` (global).
- **Best Practice**: Keep `SKILL.md` under 500 lines and use `references/` for details.

## 🔌 Plugins & Custom Tools

Extend OpenCode's functionality with custom code.

- **Plugins**: Hook into events and add complex behavior.
  - **Local Path**: `.opencode/plugins/` (project) or `~/.config/opencode/plugins/` (global).
  - See [Plugins Reference](references/opencode-plugins.md).
- **Custom Tools**: Add specific functions the LLM can call. See [Custom Tools Reference](references/opencode-tools.md).
- **Dependencies**: Use `package.json` in the config directory (`.opencode/` or `~/.config/opencode/`) to manage npm packages.

## ⚙️ Configuration & Permissions

Manage how OpenCode behaves and what it can access.

- **Files**:
  - **Global**: `~/.config/opencode/opencode.json`
  - **Project**: `opencode.json` (or `.opencode/opencode.json`) in the project root.
- **Permissions**: Control access to tools and skills (allow, ask, deny).
- **Introspection**: Use the local schema to query available settings and descriptions.
- **Details**: See [Configuration Reference](references/opencode-config.md).

### 🤖 Model Management

Synchronize models from providers to your configuration.

- **Tool**: `update-opencode-models`
- **Usage**: Run with `--api-base <url>` (e.g., `http://myhost:7860/v1`) to automatically pull in models from that host.
- **Manual Steps**:
  - Update the provider key and name in `opencode.jsonc` after running the script.
  - Restart the server when asked to use this skill to apply changes.

### 🔑 Matrix Login

Log in to a matrix homeserver and save bridge credentials to `persona.env`.

- **Tool**: `matrix-login` (prompts for homeserver, username, and password)
- Updates `BRIDGE_HOMESERVER`, `BRIDGE_USER_ID`, and `BRIDGE_ACCESS_TOKEN` in `persona.env`.
- Preserves existing env vars and comments. Uncomments commented-out keys if present.


## ⬆️ Service Management

To apply configuration changes, you may need to restart the OpenCode service.

- **Restart**: `systemctl --user restart opencode.service`
- **Logs**: `journalctl --user -u opencode.service -f`
- **Details**: See [Service Reference](references/opencode-service.md).

---

## Technical Reference Index

- [Agent Skills](references/opencode-skills.md)
- [Plugins](references/opencode-plugins.md)
- [Custom Tools](references/opencode-tools.md)
- [Configuration](references/opencode-config.md)
- [Service Management](references/opencode-service.md)
