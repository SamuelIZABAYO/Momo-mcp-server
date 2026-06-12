"""Payment provider implementations behind a single abstract interface.

MCP tools call :class:`~momo_mcp.providers.base.PaymentProvider`, never a
provider directly. Adding a provider is one new file here, zero tool changes
.
"""
