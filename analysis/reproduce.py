"""Regenerate every figure and table from the raw logs: python analysis/reproduce.py

Extra arguments are passed to each script (for example --exp E1 to draw them from one experiment).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = ["tab_headline.py", "tab_rates.py", "apple_baseline.py", "fig2_rate.py", "fig3_interarrival.py", "fig4_loss_conditions.py", "fig_loss_time.py", "tab_refresh.py"]


def main():
    here = Path(__file__).resolve().parent
    failed = [s for s in SCRIPTS if (here / s).exists() and subprocess.run([sys.executable, str(here / s), *sys.argv[1:]]).returncode]
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
