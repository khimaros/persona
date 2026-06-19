# OpenCode Agent Skills Reference

Agent skills allow OpenCode to discover and use reusable instructions. They are loaded on-demand via the `skill` tool.

## Directory Structure

A skill is a directory containing a `SKILL.md` file:
```
skill-name/
└── SKILL.md
```

Optional directories:
- `scripts/`: Executable scripts.
- `references/`: Detailed documentation.
- `assets/`: Static resources.

## SKILL.md Format

Must contain YAML frontmatter followed by Markdown.

### Required Frontmatter
```yaml
---
name: skill-name
description: What this skill does and when to use it.
---
```

- **name**: 1-64 chars, lowercase alphanumeric and hyphens. Must match directory name.
- **description**: 1-1024 chars. Used for discovery.

### Optional Frontmatter
- **license**: License name.
- **compatibility**: Environment requirements.
- **metadata**: Key-value map.

## Discovery Locations

OpenCode searches for skills in:
- Project: `.opencode/skills/<name>/SKILL.md`
- Global: `~/.config/opencode/skills/<name>/SKILL.md`
- Claude-compatible: `.claude/skills/<name>/SKILL.md`

## Best Practices (agentskills.io)

1. **Progressive Disclosure**: Keep `SKILL.md` focused (under 500 lines). Move details to `references/`.
2. **File References**: Use relative paths (e.g., `references/DETAILS.md`).
3. **Naming**: Use specific, descriptive names.
4. **Validation**: Use `skills-ref validate ./my-skill` if available.
