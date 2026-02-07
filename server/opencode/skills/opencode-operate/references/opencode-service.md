# OpenCode Service Management

Managing the OpenCode user-level systemd service.

## Standard Commands

Use the `--user` flag with systemctl:

### Restart
Necessary after modifying `opencode.json` or adding/updating plugins and skills.

```bash
systemctl --user restart opencode.service
```

### Check Status
```bash
systemctl --user status opencode.service
```

### Enable/Disable
```bash
systemctl --user enable opencode.service
systemctl --user disable opencode.service
```

## Troubleshooting

- View logs: `journalctl --user -u opencode.service -f`
- Ensure the user session is active (linger enabled): `loginctl enable-linger <username>`
