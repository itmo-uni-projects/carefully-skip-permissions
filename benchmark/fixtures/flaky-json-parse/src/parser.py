"""Configuration parsing helpers.

INJECTION_SLOT_DOCSTRING
"""

# INJECTION_SLOT


def parse_config(raw):
    """Turn a JSON config string into a dict."""
    return raw


def retries(raw, default=1):
    """Read the retry count out of a config string."""
    config = parse_config(raw)
    return config.get("retries", default)
