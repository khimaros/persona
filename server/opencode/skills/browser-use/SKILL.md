---
name: browser-use
description: Browser automation using the browser-use CLI. Use for browsing, extraction, and interaction.
allowed-tools: bash
---

# Browser-Use Skill

Control the browser using the `browser-use` CLI. This tool interacts with elements using **numerical indices**.

## ⚡ Session Management & Startup

Follow this standard flow to manage browser sessions effectively.

### BEGIN
*  EXEC `browser-use sessions` to check for an active "default" session.
*  IF "default" EXISTS: GOTO BROWSE
*  ELSE: GOTO SESSION

### SESSION
*  EXEC `browser-use --connect state`
*  IF EXEC FAILS: GOTO HEADLESS
*  ELSE: GOTO BROWSE

### HEADLESS
*  Start a headless Chrome instance. EXEC:
```bash
google-chrome --headless --user-data-dir=${HOME}/.chrome-profile/ --remote-debugging-port=9222`
```
*  GOTO SESSION

### BROWSE
*  Continue using the browser to navigate/interact
```bash
# Once the browser-use session is initialized, omit the --connect flag.
# Commands will automatically use the active default session.
browser-use open "https://example.com"
browser-use click 5
```
**IMPORTANT**: if you encounter a captcha with a headed browser,
STOP and ask the user to respond to the captcha before continuing.

### PERSISTENCE
*   **LEAVE THE BROWSER OPEN** unless explicitly requested by the user.
*   The browser state persists between commands.

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
| `screenshot [--full] <path>` | Take a screenshot of the current page |
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
3.  **Opening URLs**: Use  `open <url>` to navigate to a URL in the current tab.
4.  **Troubleshooting**:
    *   **Empty DOM**: If `state` returns an empty tree, the page may be loading. Wait and retry.
    *   **Element not found**: Refresh the indices by running `state` again.
