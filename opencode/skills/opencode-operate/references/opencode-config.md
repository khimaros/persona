# OpenCode Configuration Reference

OpenCode is configured via `opencode.json` files.

## Locations

1. `~/.config/opencode/opencode.json` (Global configuration)
2. `opencode.json` or `.opencode/opencode.json` (Project-specific configuration)
3. `~/.config/opencode/agents/` (Agent-specific markdown overrides)

## Key Configuration Sections

### Permissions (`permission`)

Control tool and skill access. Actions: `"allow"`, `"ask"`, `"deny"`.

```json
{
  "permission": {
    "bash": { "git *": "allow" },
    "skill": {
      "internal-*": "deny",
      "*": "allow"
    }
  }
}
```

### Agents (`agent`)

Override settings for specific agents. Common keys include `mode`, `description`, `prompt`, `tools`, and `permission`. 

**Note on `prompt`**: If an agent has a `prompt` field, it is often injected as a distinct message or system prompt fragment. To completely control the system prompt via a plugin, you may need to filter these redundant prompts in `experimental.chat.messages.transform`.

```json
{
  "agent": {
    "plan": {
      "permission": { "skill": { "internal-*": "allow" } }
    }
  }
}
```

### Plugins (`plugin`)

List of npm plugins to load.

```json
{
  "plugin": ["opencode-wakatime"]
}
```

## Advanced Customization

- `tools`: Enable/disable specific tools (e.g., `"skill": false`).
- `commands`: Define custom slash commands.
- `rules`: Project-specific instructions.

## Schema & Introspection

OpenCode uses a JSON schema to validate `opencode.json`. A local copy is maintained in this skill's assets.

- **Schema Location**: `~/.config/opencode/skills/opencode-operate/assets/opencode-config.schema.json`

### Introspection Commands

You can use `jq` to query the schema for information about specific configuration keys.

#### 1. List all available configuration keys
```bash
jq -r '.properties | keys[]' ~/.config/opencode/skills/opencode-operate/assets/opencode-config.schema.json
```

#### 2. Get description and type for a specific key
```bash
# Example for 'logLevel'
jq '.properties.logLevel | {description, type, enum}' ~/.config/opencode/skills/opencode-operate/assets/opencode-config.schema.json
```

#### 3. Explore Agent settings
```bash
# View available properties for agents
jq '.properties.agent.additionalProperties.properties | keys[]' ~/.config/opencode/skills/opencode-operate/assets/opencode-config.schema.json
```

#### 4. Find all keys that have an 'enum' (restricted values)
```bash
jq -r '.properties | to_entries[] | select(.value.enum != null) | .key' ~/.config/opencode/skills/opencode-operate/assets/opencode-config.schema.json
```

#### 5. Verify your current config against a key's allowed values
```bash
# Check if your current logLevel is valid
CURRENT_LOG=$(jq -r '.logLevel // "INFO"' ~/.config/opencode/opencode.json)
jq -e ".properties.logLevel.enum | contains([\"$CURRENT_LOG\"])" ~/.config/opencode/skills/opencode-operate/assets/opencode-config.schema.json
```

