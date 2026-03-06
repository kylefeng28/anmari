"""
Python wrapper for Rust cache backend.

This module provides a drop-in replacement for cache.py using the Rust implementation.
Import this instead of cache.py to use the faster Rust backend.

Usage:
    from cache_rs import EmailCache, CachedMessage, CachedFolderState
"""

from anmari_rs import PyEmailCache as EmailCache, PyCachedMessage as CachedMessage

__all__ = ['EmailCache', 'CachedMessage']
