"""WP-08: local copies, retrieval and verification (spec 10.3, 10.4)."""

from .local_copies import DefaultLocalCopyChecker
from .netguard import DefaultNetGuard
from .retrieval import DefaultRetriever
from .verify import DefaultFileVerifier

__all__ = ["DefaultFileVerifier", "DefaultLocalCopyChecker", "DefaultNetGuard", "DefaultRetriever"]
