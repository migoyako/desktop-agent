"""MCP client: transports, discovery and the bridge into the tool registry."""

from .jsonrpc import (
    PROTOCOL_VERSION,
    McpError,
    McpProtocolError,
    McpRemoteError,
    McpTimeout,
)
from .service import (
    TRANSPORT_HTTP,
    TRANSPORT_STDIO,
    McpCallResult,
    McpServerSpec,
    McpService,
)

__all__ = [
    "PROTOCOL_VERSION",
    "TRANSPORT_HTTP",
    "TRANSPORT_STDIO",
    "McpCallResult",
    "McpError",
    "McpProtocolError",
    "McpRemoteError",
    "McpServerSpec",
    "McpService",
    "McpTimeout",
]
