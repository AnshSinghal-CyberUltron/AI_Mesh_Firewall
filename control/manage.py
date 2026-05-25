#!/usr/bin/env python
"""Django CLI — AI Mesh Firewall control plane."""
import os
import sys
from pathlib import Path

def main():
    base = Path(__file__).resolve().parent
    sys.path.insert(0, str(base / "ai_mesh_control"))
    sys.path.insert(0, str(base.parent / "shared"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()
