#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility wrapper for direct script usage and old imports."""

from parallel_codex_runner_core import *  # noqa: F401,F403
from parallel_codex_runner_core.app import main


if __name__ == "__main__":
    raise SystemExit(main())
