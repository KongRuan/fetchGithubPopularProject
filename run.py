"""Entry point: python run.py [fetch|merge|render]"""
import sys

from src.rank.cli import main

if __name__ == "__main__":
    sys.exit(main())
