"""Smoke-test fixture for the auditor review gate. Safe to delete — DO NOT MERGE.

Opened as a deliberately reviewable PR to confirm the auditor reviews
system-tier changes end-to-end (tier -> model -> PR comment). The function
below has intentional rough edges a real reviewer should flag.
"""


def parse_retry_after(header_value):
    """Parse a Retry-After header into an integer number of seconds."""
    return int(header_value)
