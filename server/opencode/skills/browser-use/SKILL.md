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
| `new-tab` | Open a new blank tab |
| `open <url>` | Navigate to a URL in the current tab |
| `back` | Go back in history |
| `switch <tab_id>` | Switch to a specific tab |
| `close-tab` | Close the current tab |

### Inspection
| Command | Description |
| :--- | :--- |
| `state` | **CRITICAL**: Get current URL, title, and interactive elements with indices |
| `screenshot [--full] <path>` | Take a screenshot (base64 if no path) |
| `get title` | Get page title |
| `get html` | Get full page HTML |
| `get html --selector "css"` | Get HTML of matching element |
| `get text <index>` | Get text content of element |
| `get value <index>` | Get value of input/textarea |
| `get attributes <index>` | Get all attributes of element |
| `get bbox <index>` | Get bounding box (x, y, width, height) |

### Interaction
| Command | Description |
| :--- | :--- |
| `click <index>` | Click an element by its index |
| `type <text>` | Type text into the currently focused element |
| `input <index> <text>` | Type text into a specific element by index |
| `keys <key>` | Send special keys (e.g., `Enter`, `Tab`) |
| `scroll <amount>` | Scroll page (positive for down, negative for up) |
| `select <index> <option>` | Select a dropdown option |
| `hover <index>` | Hover over an element |
| `dblclick <index>` | Double-click an element |
| `rightclick <index>` | Right-click an element |

### Advanced
| Command | Description |
| :--- | :--- |
| `eval <js>` | Execute JavaScript in the browser |
| `extract <query>` | Extract structured data from the page using LLM |
| `wait selector "css"` | Wait for element to be visible |
| `wait selector "css" --state hidden` | Wait for element to disappear |
| `wait text "string"` | Wait until text appears on page |

**Tip**: Use `eval "window.open('https://example.com', '_blank')"` to open a link in a new tab.

### Management
| Command | Description |
| :--- | :--- |
| `sessions` | List active sessions |
| `close` | Close the session (only use if explicitly asked) |
| `install` | Install Chromium browser + system dependencies |

## 📖 Core Rules

1.  **`state` vs `get`**: Use `state` to discover interactive elements and their indices before clicking/typing. Use `get text`, `get html`, or `get value` to read content from a specific element you already know the index of. `state` is for *what can I interact with?*, `get` is for *what does this element contain?*
2.  **Handling Forms**: Use `input <index> "text"` to fill fields, then `click <index>` or `keys Enter` to submit.
3.  **Opening URLs**: Use  `open <url>` to navigate to a URL in the current tab.
4.  **Troubleshooting**:
    *   **Empty DOM**: If `state` returns an empty tree, the page may be loading. Wait and retry.
    *   **Element not found**: Refresh the indices by running `state` again.
