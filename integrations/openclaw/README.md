# OpenClaw / ClawBot Bridge

This directory connects the running desktop companion (数字分身) to the existing OpenClaw Gateway. WeChat messages are answered by the same execution kernel as the desktop UI: persona, long-term memory, permissions and receipts are shared, and every WeChat turn is visible in the desktop chat (decision D6).

## Data flow

```text
WeChat mobile
  -> ClawBot
  -> OpenClaw Gateway
  -> current-agent/python-agent
  -> agent_service.py
  -> data/.companion.endpoint (port + token)
  -> running companion web adapter (/api/bridge/turn)
  -> CompanionRuntime (current desktop session)
```

## Prerequisites

The desktop companion must be running with its web UI (the default):

```powershell
python companion.py
```

On startup it writes `data/.companion.endpoint` (loopback port + access token) and removes it on exit. The bridge reads this file to find the instance; it never starts a second runtime, so the single-instance lock and the shared database stay intact. If the companion is not running, WeChat receives a notice asking to start it first.

## Start the local service

From the project root:

```powershell
python agent_service.py
```

The model, offline mode and all other settings come from the running companion (settings page / `config/keys.json`), not from the bridge. If the companion uses a custom data directory, pass the same one: `python agent_service.py --data-dir <dir>`. The endpoint is:

```text
http://127.0.0.1:8765/v1
```

## Register it with OpenClaw

Validate the patch first:

```powershell
openclaw config patch --file .\integrations\openclaw\local_agent.patch.json --dry-run
```

Apply it:

```powershell
openclaw config patch --file .\integrations\openclaw\local_agent.patch.json
openclaw gateway restart
```

Verify the provider:

```powershell
openclaw models list --provider current-agent
openclaw agent --message "你好"
```

The reply must come from the companion (in offline mode it says so explicitly), and the same turn appears in the desktop chat.

## Behavior notes

- Messages land in the companion's **current desktop session**; phone and desktop continue one conversation.
- While the desktop is generating a reply, new WeChat messages get a "reply already in progress" notice instead of queueing.
- If a turn produces a write proposal, the plan text is included in the WeChat reply; confirm or cancel it in the desktop UI.
- The bridge waits up to 110s (the endpoint waits 100s); OpenClaw's provider timeout is 120s. A turn that outlives the wait keeps running on the desktop, and WeChat is told to check there.

## Keep it running

For a temporary foreground process, keep the terminal open. For a background process on Windows:

```powershell
Start-Process -FilePath python -ArgumentList "agent_service.py" -WorkingDirectory "C:\Users\黄家禄\Desktop\hjl\AI\Agent" -WindowStyle Hidden
```

The OpenClaw Gateway, the bridge and the desktop companion must all remain running. If the computer sleeps or any of them exits, WeChat messages will not be processed (without the companion, the bridge replies with the start-it-first notice).

## Sessions

OpenClaw's `user` / `X-OpenClaw-Session-Id` keys are accepted but no longer isolate agents: every message goes to the companion's current desktop session, which persists in `data/companion.db`. This matches a single personal WeChat user continuing the same conversation on phone and desktop. Per-WeChat-user sessions would need runtime support for multiple concurrent sessions and are out of scope.
