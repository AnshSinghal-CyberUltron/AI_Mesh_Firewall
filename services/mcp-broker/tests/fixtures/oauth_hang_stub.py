#!/usr/bin/env python3
"""Stdio child that prints an interactive OAuth prompt (for hang-detection tests)."""

import sys
import time

prompt = "Please visit https://example.com/oauth to authorize this app"
print(prompt, flush=True)
sys.stderr.write(prompt + "\n")
sys.stderr.flush()
time.sleep(300)
