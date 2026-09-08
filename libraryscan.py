#!/usr/bin/env python3
"""Entry point for the Ebook Library scanner.

The implementation lives in the ``ebooklibrary`` package; this stays put so
existing commands and shell aliases keep working.
"""
import sys

from ebooklibrary.cli import main

if __name__ == '__main__':
    sys.exit(main())
