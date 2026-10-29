#!/usr/bin/env python3
"""Start the local finance MCP stdio facade; configure env per dedicated session.

Required: FINANCE_AI_TOKEN or FINANCE_AI_TOKEN_FILE (0600). Optional: FINANCE_API_BASE_URL (loopback HTTP origin).
Does not install a global Codex configuration or start the finance application.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.finance_mcp import main

if __name__ == '__main__':
    main()
