---
name: browser-use
description: Browser automation using the browser-use CLI. Use for browsing, extraction, and interaction.
allowed-tools: bash
---

# Browser-Use Skill

Control the browser using the `browser-use` CLI. This tool interacts with elements using **numerical indices**.

## ⚡ Session Management & Startup

Follow this standard flow to manage browser sessions effectively.

### 1. Check Existing Session
Run `browser-use sessions` to check for an active "default" session.
*   **If "default" exists:** Use it immediately. **DO NOT** create a new session.
*   **If "default" does NOT exist:** Proceed to step 2.

### 2. Create Session (Headless)
Start a headless session automatically — do not ask the user.
```bash
browser-use --user-data-dir ${HOME}/.browser-use/ new-tab
```
If the user wants a headed (visible) session, they will start one manually before invoking this skill.

### 3. Subsequent Commands
Once the session is initialized, omit the user data flags. Commands will automatically use the active default session.
```bash
browser-use open "https://example.com"
browser-use click 5
```

### 4. Persistence
*   **DO NOT CLOSE** the session unless explicitly requested by the user.
*   The browser state persists between commands.

---

## 🛠️ Command Reference

### Navigation
| Command | Description |
| :--- | :--- |
| `open <url>` | Navigate to a URL in the current tab |
| `new-tab` | Open a new blank tab |
| `back` | Go back in history |
| `switch <tab_id>` | Switch to a specific tab |
| `close-tab` | Close the current tab |

### Inspection
| Command | Description |
| :--- | :--- |
| `state` | **CRITICAL**: Get current URL, title, and interactive elements with indices |
| `screenshot` | Take a screenshot of the current page |
| `get` | Get specific information |
| `cookies` | Perform cookie operations |

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
| `eval <js>` | Execute JavaScript in the browser (only if explicitly requested) |
| `extract <query>` | Extract data from the page using LLM (only if explicitly requested) |
| `python <code>` | Execute Python code with browser access (only if explicitly requested) |
| `wait <condition>` | Wait for specific conditions |

### Management
| Command | Description |
| :--- | :--- |
| `sessions` | List active sessions |
| `close` | Close the session (only use if explicitly asked) |
| `install` | Install Chromium browser + system dependencies |

## 📖 Core Rules

1.  **Check State Frequently**: Always run `state` before interacting to ensure you have correct element indices, as they change when the page updates.
2.  **Handling Forms**: Use `input <index> "text"` to fill fields, then `click <index>` or `keys Enter` to submit.
3.  **Opening URLs**: Use `new-tab` to open a blank tab, then `open <url>` to navigate. To open a URL in the current tab, just use `open <url>` directly.
4.  **Troubleshooting**:
    *   **Empty DOM**: If `state` returns an empty tree, the page is likely loading. Wait and retry.
    *   **Element not found**: Refresh the indices by running `state` again.
