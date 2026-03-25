#!/usr/bin/env python
"""
myscoop.py — Entry point for the myscoop package manager.

Run:  python myscoop.py <command> [args]
      python myscoop.py install mysqlworkbench
      python myscoop.py list
      python myscoop.py search mysql
"""

from myscoop.cli import main

if __name__ == "__main__":
    main()
