"""
Heartbeat subsystem: refresh agent memory management.

Key functions: main
"""
import argparse
import subprocess
from pathlib import Path

import sys
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import config as _cfg

WORKSPACE = _cfg.WORKSPACE
SCRIPTS_DIR = WORKSPACE / ".memory-index" / "scripts"
REFRESH_SCRIPT = SCRIPTS_DIR / "refresh_agent_memory.py"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    cmd = [
        "python3",
        str(REFRESH_SCRIPT),
        "--agent",
        args.agent,
    ]
    if args.force:
        cmd.append("--force")

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
