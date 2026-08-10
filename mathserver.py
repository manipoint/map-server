from fastmcp import FastMCP

# Create an MCP server named "Math". MCP clients use this server to discover
# and call the tools registered below.
mcp = FastMCP("Math")


@mcp.tool
def add_numbers(a: int, b: int) -> int:
    """Add two integers and return their sum."""
    return a + b


@mcp.tool
def multiply_numbers(a: int, b: int) -> int:
    """Multiply two integers and return their product."""
    return a * b


if __name__ == "__main__":
    # This block runs only when this file is executed directly, for example:
    #     uv run python mathserver.py
    #
    # The STDIO transport communicates with an MCP client through the
    # terminal process's standard input and standard output streams. This is
    # useful for local MCP clients and command-line testing because no HTTP
    # server, host, or port is required.
    #
    # Avoid using print() for debugging while STDIO transport is active:
    # arbitrary stdout text can corrupt the MCP protocol messages. Write logs
    # to stderr or use Python's logging module instead.
    mcp.run(transport="stdio")
