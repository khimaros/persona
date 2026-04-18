---
name: browser-use
description: Browser automation using the browser-use CLI. Use for browsing, extraction, and interaction.
allowed-tools: bash
---

# Browser-Use Skill

Control the browser using the `browser-use` CLI. This tool interacts with elements using **numerical indices**.

## ⚡ Session Management

*  EXEC `browser-head start` to ensure a browser session is running. It reuses an existing session or launches chrome and connects browser-use automatically. Headless or headed mode is selected based on `DISPLAY`.
*  Once connected, use browser-use commands directly:
```bash
browser-use open "https://example.com"
browser-use click 5
```

| Command | Description |
| :--- | :--- |
| `browser-head start` | Start chrome and connect browser-use (reuses existing session) |
| `browser-head stop` | Close browser-use session and kill chrome |
| `browser-head restart` | Stop then start |
| `browser-head status` | Check if chrome and browser-use session are running |
| `browser-head wait` | Block until chrome exits, then close the browser-use session |

**IMPORTANT**: if you encounter a captcha with a headed browser,
STOP and ask the user to respond to the captcha before continuing.

**LEAVE THE BROWSER OPEN** unless explicitly requested by the user.

---

## 🛠️ Command Reference

### Navigation
| Command | Description |
| :--- | :--- |
| `open <url>` | Navigate to a URL in the current tab |
| `back` | Go back in history |
| `tab list` | List all open tabs |
| `tab new [url]` | Open a new tab (optionally with a URL) |
| `tab switch <index>` | Switch to a specific tab by index |
| `tab close [index...]` | Close one or more tabs by index |

### Inspection
`state` and `screenshot` inspect the whole page. `get <subcommand>` retrieves
specific info — it is **not** a standalone command; you must pass a subcommand.
`<index>` values come from running `state` first.

| Command | Description |
| :--- | :--- |
| `state` | Page URL, title, and interactive elements with their indices. JS dialogs auto-dismiss and appear here. Run this before any `click`/`input`/`get` that needs an index |
| `screenshot [path] [--full]` | Take a PNG screenshot. With `path`, writes the file and prints the path; without `path`, prints raw base64 (no `data:` prefix) to stdout. `--full` captures the entire scrollable page instead of just the viewport |
| `get title` | Page `<title>` |
| `get html [--selector "css"]` | Full page HTML, or HTML of the first element matching the CSS selector |
| `get text <index>` | Visible text content of the element |
| `get value <index>` | Current value of an `<input>`/`<textarea>`/`<select>` |
| `get attributes <index>` | All HTML attributes of the element as a dict |
| `get bbox <index>` | Bounding box `{x, y, width, height}` in CSS pixels |

### Interaction
| Command | Description |
| :--- | :--- |
| `click <index>` \| `click <x> <y>` | Click by element index, or by viewport pixel coordinates when two integers are given |
| `type <text>` | Type text into the currently focused element |
| `input <index> <text>` | Type text into a specific element by index |
| `keys <keys>` | Send a key or chord. Single keys (`Enter`, `Tab`, `Escape`, `ArrowDown`) or combos joined with `+` (`Control+a`, `Shift+Tab`, `Meta+k`) |
| `scroll [up\|down] [--amount N]` | Scroll page. direction defaults to `down`; `--amount` is pixels (default 500) |
| `select <index> <option>` | Select a dropdown option |
| `hover <index>` | Hover over an element |
| `dblclick <index>` | Double-click an element |
| `rightclick <index>` | Right-click an element |

### Advanced
| Command | Description |
| :--- | :--- |
| `eval <js>` | Execute JavaScript in the browser |
| `extract <query>` | Extract structured data from the page using LLM |
| `wait selector "css" [--state S] [--timeout MS]` | Wait for a selector. `--state` is `attached`, `detached`, `visible` (default), or `hidden`. `--timeout` in milliseconds (default 30000) |
| `wait text "string" [--timeout MS]` | Wait until text appears on the page. `--timeout` in milliseconds (default 30000) |

**Tip**: Use `tab new https://example.com` to open a link in a new tab.

### Management
| Command | Description |
| :--- | :--- |
| `sessions` | List active sessions |
| `close` | Close the session (only use if explicitly asked) |
| `install` | Install Chromium browser + system dependencies |

## 📖 Core Rules

1.  **`state` vs `get`**: Use `state` to discover interactive elements and their indices before clicking/typing. Use `get text`, `get html`, or `get value` to read content from a specific element you already know the index of. `state` is for *what can I interact with?*, `get` is for *what does this element contain?*
2.  **Handling Forms**: Use `input <index> "text"` to fill fields, then `click <index>` or `keys Enter` to submit.
3.  **Opening URLs**: Use `open <url>` to navigate in the current tab, or `tab new <url>` for a new tab.
4.  **Troubleshooting**:
    *   **Empty DOM**: If `state` returns an empty tree, the page may be loading. Wait and retry.
    *   **Element not found**: Refresh the indices by running `state` again.
