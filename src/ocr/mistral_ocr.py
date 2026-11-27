from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from src.ocr.base import OCRBackend, OCRResult
from src.provider_http import ProviderError, request_json

OCR_MODEL = "mistral-ocr-latest"
MAX_PDF_BYTES = 50_000_000


class MistralOCRBackend(OCRBackend):
    """Extract review candidates from a user-selected local PDF."""

    def __init__(self, api_key: str | None = None, enabled: bool = True):
        self.api_key = api_key if api_key is not None else os.getenv("MISTRAL_API_KEY")
        self.enabled = enabled

    def extract_document(self, file_path: Path, *, pages: list[int] | None = None) -> OCRResult:
        if not self.enabled or not self.api_key:
            return OCRResult(extraction_method="mistral_ocr_skipped")

        with file_path.open("rb") as source:
            content = source.read(MAX_PDF_BYTES + 1)
        if not content.startswith(b"%PDF-") or len(content) > MAX_PDF_BYTES:
            raise ValueError("Mistral OCR: требуется PDF объёмом до 50 МБ")
        if pages is not None and (not pages or any(type(page) is not int or page < 0 for page in pages)):
            raise ValueError("Mistral OCR: номера страниц начинаются с нуля")

        encoded = base64.b64encode(content).decode("ascii")
        payload = {
            "model": OCR_MODEL,
            "document": {"type": "document_url", "document_url": f"data:application/pdf;base64,{encoded}"},
            "include_image_base64": False,
            "include_blocks": False,
            "confidence_scores_granularity": "page",
        }
        if pages is not None:
            payload["pages"] = pages
        response = request_json(
            "POST", "https://api.mistral.ai/v1/ocr", "Mistral OCR",
            headers={"Authorization": f"Bearer {self.api_key}"}, json=payload,
        )
        extracted = response.get("pages")
        if not isinstance(extracted, list) or not extracted or any(
            not isinstance(page, dict) or type(page.get("index")) is not int
            or not isinstance(page.get("markdown"), str) for page in extracted
        ):
            raise ProviderError("Mistral OCR: в ответе отсутствуют страницы распознавания")
        if pages is not None and sorted(page["index"] for page in extracted) != sorted(set(pages)):
            raise ProviderError("Mistral OCR: номера страниц в ответе отличаются от запроса")
        extracted = sorted(extracted, key=lambda page: page["index"])
        markdown = "\n\n".join(page["markdown"] for page in extracted)
        return OCRResult(
            pages=extracted, markdown=markdown, text=markdown, extraction_method="mistral_ocr",
            model=response.get("model") or OCR_MODEL, source_sha256=hashlib.sha256(content).hexdigest(),
        )
