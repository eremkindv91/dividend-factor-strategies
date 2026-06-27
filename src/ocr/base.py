from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OCRResult:
    pages: list[dict] = field(default_factory=list)
    blocks: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    text: str = ""
    markdown: str = ""
    confidence: float = 0.0
    extraction_method: str = "ocr"


class OCRBackend:
    def extract_document(self, file_path: Path) -> OCRResult:
        raise NotImplementedError

