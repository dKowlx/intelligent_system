"""Shared limits for project material input and PDF extraction."""

MAX_TEXT_CHARACTERS = 100_000
# Escaped non-BMP JSON uses two six-byte Unicode escapes per character.
MAX_TEXT_REQUEST_BYTES = MAX_TEXT_CHARACTERS * 12 + 65_536
