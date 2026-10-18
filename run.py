"""Entry point: ``python run.py [fetch|merge|render]``.

Thin shim so the pipeline runs from the repo root without installing the
package. The CLI itself lives in ``src.rank.cli``.
"""
import sys

from src.rank.cli import main

if __name__ == "__main__":
    sys.exit(main())
