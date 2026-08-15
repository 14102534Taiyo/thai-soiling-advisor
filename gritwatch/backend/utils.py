from __future__ import annotations

from datetime import date, datetime

import pandas as pd


def to_date(value) -> date:
    """pandas/economics.py hand back pd.Timestamp for date-like values --
    normalize to a plain date for the JSON API."""
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    return value
