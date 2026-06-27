from __future__ import annotations

import os
from pathlib import Path

from src.ocr.base import OCRBackend, OCRResult


class MistralOCRBackend(OCRBackend):
    """Optional OCR backend.

    It intentionally returns an empty result when MISTRAL_API_KEY is absent so
    the pipeline can keep using SmartLab fallback without failing.
    """

    def __init__(self, api_key: str | None = None, enabled: bool = True):
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY")
        self.enabled = enabled

    def extract_document(self, file_path: Path) -> OCRResult:
        if not self.enabled or not self.api_key:
            return OCRResult(confidence=0.0, extraction_method="mistral_ocr_skipped")
        raise RuntimeError(f"Mistral OCR network call is not enabled in this safe baseline stage: {file_path}")

