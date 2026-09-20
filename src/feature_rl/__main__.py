"""Run the actual thin CLI without requiring console-entry metadata."""
from .cli import main

raise SystemExit(main())
