# OpenClaw / ClawBot Bridge

This directory connects the current Python `ChatAgent` to the existing OpenClaw Gateway.

## Data flow

```text
WeChat mobile
  -> ClawBot
  -> OpenClaw Gateway
  -> current-agent/python-agent
  -> agent_service.py
  -> agent_framework.ChatAgent
```

## Start the local service

From the project root:

```powershell
python agent_service.py
```

The service automatically uses an external model when `config/keys.json` or model environment variables are configured, and falls back to offline tools otherwise. The endpoint is:

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
openclaw agent --message "计算 12 * (3 + 4)"
```

The expected answer is `84`.

## Keep it running

For a temporary foreground process, keep the terminal open. For a background process on Windows:

```powershell
Start-Process -FilePath python -ArgumentList "agent_service.py" -WorkingDirectory "C:\Users\黄家禄\Desktop\hjl\AI\Agent" -WindowStyle Hidden
```

The OpenClaw Gateway must also remain running. If the computer sleeps or either process exits, WeChat messages will not be processed.

## Sessions

OpenClaw messages are routed to one local service. `user` or `X-OpenClaw-Session-Id` is used as the session key when present; otherwise all requests use the same `default` session. This is sufficient for a single personal WeChat user. For multiple users, add a stable session identifier and persistent storage.
