"""Package for the pipeline monitor's MCP server.

Named `lm_mcp` rather than `mcp` deliberately: a package named `mcp` sitting in
the working directory shadows the widely-installed `mcp` SDK for anything run
from this directory, which would be a confusing failure with no obvious cause.
This server takes no SDK dependency, so it has no reason to occupy that name.
"""
