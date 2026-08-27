"""Shared runtime and validator limits."""

# Q&A A16/A21/A75 require literal complete enumeration.  The released large
# path cases are in the hundreds of thousands, so the old 5,000 cap produced a
# knowingly incomplete answer.  This remains a safety ceiling for malformed or
# explosive hidden graphs, not a normal response truncation threshold.
DEFAULT_COMPLETE_PATH_LIMIT = 1_000_000
