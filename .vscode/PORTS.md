# Cursor localhost ports (SSH tunnel-forwarding)

When you open this repo over **Cursor Remote SSH**, Chrome on your laptop can use:

- http://localhost:8180/ and http://127.0.0.1:8180/login
- http://localhost:8180/demo/

That is **not** GNOME. It is Cursor’s built-in **tunnel-forwarding**
(`vscode.tunnel-forwarding` / Ports panel): SSH `LocalForward` tunnels
laptop → VM for the duration of the Cursor SSH session.

Closing the remote connection drops those tunnels → `ERR_CONNECTION_REFUSED`
on laptop localhost until they are restored.

## Restore after reconnect

1. Command Palette → **Developer: Reload Window** (or reconnect Remote SSH).
2. Open **Ports** view — 8180 / 8770 / 8100 / 8300 should show as forwarded.
3. If empty: **Forward a Port** → `8180` (and the others).

Workspace settings already set:

- `remote.SSH.defaultForwardedPorts` for 8180/8770/8100/8300
- `remote.restoreForwardedPorts`: true
- `remote.autoForwardPorts`: true

If workspace defaults are ignored by your Cursor build, paste the same
`remote.SSH.defaultForwardedPorts` block into **local** Cursor
**User** `settings.json` (on the laptop, not the VM).

## Optional: always-on SSH config (laptop `~/.ssh/config`)

```
Host ai-mesh-firewall
    HostName 8.231.115.48
    User contact_cyberultron_com
    LocalForward 127.0.0.1:8180 127.0.0.1:8180
    LocalForward 127.0.0.1:8770 127.0.0.1:8770
    LocalForward 127.0.0.1:8100 127.0.0.1:8100
    LocalForward 127.0.0.1:8300 127.0.0.1:8300
```

Or run `scripts/dev/ssh_local_ui_forward.sh` from the laptop.
