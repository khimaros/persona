# OpenCode Plugins Reference

Plugins extend OpenCode by hooking into events and adding custom tools.

## Installation

- **Local**: Place `.js` or `.ts` files in `.opencode/plugins/` or `~/.config/opencode/plugins/`.
- **npm**: Add to `plugin` array in `opencode.json`. Installed automatically via Bun.

## Structure

A plugin is a module that exports one or more functions.

```typescript
import type { Plugin } from "@opencode-ai/plugin";

export const MyPlugin: Plugin = async ({ project, client, $, directory, worktree }) => {
  return {
    "session.created": async (session) => {
      // Logic
    }
  };
};
```

## Dependencies

Local plugins can use npm packages if a `package.json` exists in the config directory (`.opencode/` or `~/.config/opencode/`). OpenCode runs `bun install` at startup.

## Common Event Hooks

- `session.created`, `session.idle`, `session.compacted`
- `tool.execute.before`, `tool.execute.after`
- `command.executed`
- `file.edited`
- `experimental.session.compacting`: Customize or replace the compaction prompt.
- `experimental.chat.system.transform`: Modify or replace the system prompt array. Useful for injecting persona data or custom environment blocks.
- `experimental.chat.messages.transform`: Filter or modify the message history before it is sent to the LLM. Ideal for removing system-injected messages (e.g., default environment blocks).

## Advanced Plugin Techniques

### Robust Agent Identification
Hooks often receive `sessionID`. Use it to fetch the session and identify the active agent to apply agent-specific logic.

```typescript
async function getAgentName(client, sessionID) {
  if (!sessionID) return null;
  const { data: session } = await client.session.get({ path: { id: sessionID } });
  return session?.agent || null;
}
```

### System Prompt Control
To completely replace the system prompt, use `.splice()` on `output.system`. To replace the default OpenCode environment block, clear `output.env`.

```typescript
"experimental.chat.system.transform": async (input, output) => {
  // Clear standard system messages
  output.system.splice(0, output.system.length, "My new system prompt");
  
  // Suppress default environment block
  if (output.env) {
    for (const key in output.env) delete output.env[key];
  }
}
```

### Message Filtering
Use `experimental.chat.messages.transform` to prune unwanted messages injected by the core system.

```typescript
"experimental.chat.messages.transform": async (input, output) => {
  if (!output.messages) return;
  const filtered = output.messages.filter(msg => {
    const content = (msg.parts || []).map(p => p.text || "").join("");
    // Filter out default OpenCode environment block
    return !(content.includes("<env>") && content.includes("Working directory:"));
  });
  output.messages.splice(0, output.messages.length, ...filtered);
}
```

## Best Practices

- Use `client.app.log()` for structured logging.
- **CRITICAL**: Use `tool.schema` instead of importing `z` from `@opencode-ai/plugin` or the standard `zod` package to ensure compatibility with the OpenCode runtime.

### Example: Custom Tool in Plugin
```typescript
import { type Plugin, tool } from "@opencode-ai/plugin"

export const CustomToolsPlugin: Plugin = async (ctx) => {
  return {
    tool: {
      mytool: tool({
        description: "This is a custom tool",
        args: {
          foo: tool.schema.string(),
        },
        async execute(args, context) {
          return `Hello ${args.foo}`
        },
      }),
    },
  }
}
```
