"""
Domain layer containing reusable data, NLP, and chunking logic.

This package is intentionally free of web framework dependencies so it can be
reused by other interfaces or projects.
"""

from . import collections, chunks, library

__all__ = [
    "collections",
    "chunks",
    "library",
]
