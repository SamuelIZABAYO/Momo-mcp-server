"""Payment provider implementations behind a single abstract interface.

MCP tools call :class:`~momo_mcp.providers.base.PaymentProvider`, never a
provider directly. MTN is the concrete implementation; Airtel is a stub on the
same contract. Adding a provider is one new file with no tool changes.
"""
