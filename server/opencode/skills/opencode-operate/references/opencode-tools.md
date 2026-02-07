# OpenCode Custom Tools Reference

Custom tools allow the LLM to call your functions during conversations.

## Creation

Tools are defined as JavaScript or TypeScript files in:
- Project: `.opencode/tools/`
- Global: `~/.config/opencode/tools/`

## Structure

Use the `tool()` helper from `@opencode-ai/plugin`.

### Single Tool (Default Export)
The filename becomes the tool name.
```typescript
import { tool } from "@opencode-ai/plugin";

export default tool({
  description: "Tool description",
  args: {
    param: tool.schema.string().describe("Param description")
  },
  async execute(args, context) {
    return `Result: ${args.param}`;
  }
});
```

### Multiple Tools (Named Exports)
Tools are named `<filename>_<exportname>`.
```typescript
export const add = tool({ ... });
export const multiply = tool({ ... });
```

## Arguments (Zod)

Use `tool.schema` (which is Zod) to define arguments.
```typescript
args: {
  count: tool.schema.number().min(1)
}
```

## Context

Tools receive a context object:
- `agent`: Current agent name.
- `sessionID`: Current session ID.
- `directory`: Current working directory.
- `worktree`: Git worktree root.

## Polyglot Support

The tool definition (TS/JS) can invoke scripts in any language (Python, Bash, etc.) using `Bun.$`.
