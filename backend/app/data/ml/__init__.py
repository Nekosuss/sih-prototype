"""
Part 14 (ML dataset construction) — deliberately isolated from the rest of
the backend. Nothing under app/data/ml/ is imported by app/core/, app/api/,
app/simulation/, or app/main.py: this package only builds an offline
feature table for future model development. See
app/data/ml_dataset_inspection_part14.md for the inspection this is built
from, and app/data/derived/segment_year_dataset_audit.md for the output
coverage audit.

The existing production risk engine (core/risk_engine.py) and routing
engine (core/routing_engine.py) are unchanged by this package and never
import from it.
"""
