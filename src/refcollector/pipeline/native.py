"""Run complete native adapter calls on one process-lifetime owner thread.

PyMuPDF does not support calls from multiple threads:
https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html
The shared worker is not owned or shut down by an AppHandle. Python joins it at exit.
"""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from refcollector.contracts.ports import (
    ExtractionAdapter,
    ExtractionResult,
    FileVerifier,
    VerificationResult,
)
from refcollector.domain.enums import SourceType
from refcollector.domain.models import Preflight, SectionInfo, WorkMetadata

_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="refcollector-native")


class NativeExtractor:
    """Keep each delegate's complete open/read/close lifetime on the native thread."""

    def __init__(self, delegate: ExtractionAdapter) -> None:
        self._delegate = delegate

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def version(self) -> str:
        return self._delegate.version

    def preflight(self, data: bytes | str, kind: SourceType) -> Preflight:
        return _WORKER.submit(self._delegate.preflight, data, kind).result()

    def extract(
        self, data: bytes | str, kind: SourceType, sections: Sequence[SectionInfo] | None = None
    ) -> ExtractionResult:
        return _WORKER.submit(self._delegate.extract, data, kind, sections).result()


class NativeVerifier:
    """Return only the detached verification result after the native delegate finishes."""

    def __init__(self, delegate: FileVerifier) -> None:
        self._delegate = delegate

    def verify(
        self, path: Path, work: WorkMetadata, *, content_type: str | None = None
    ) -> VerificationResult:
        return _WORKER.submit(self._delegate.verify, path, work, content_type=content_type).result()
