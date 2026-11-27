"""Verify credentials from server-side environment variables."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from news.generate_news import DEFAULT_MODEL
from src.data_sources.finam_adapter import latest_quote
from src.ocr.mistral_ocr import MistralOCRBackend
from src.provider_http import ProviderError, request_json


def check_gemini() -> None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ProviderError("Gemini: отсутствует GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).removeprefix("models/")
    if not model or not all(char.isascii() and (char.isalnum() or char in "-_.") for char in model):
        raise ValueError("Gemini: некорректное имя модели")
    details = request_json(
        "GET", f"https://generativelanguage.googleapis.com/v1beta/models/{model}", "Gemini: метаданные модели",
        headers={"x-goog-api-key": key},
    )
    if "generateContent" not in (details.get("supportedGenerationMethods") or []):
        raise ProviderError("Gemini: выбранная модель не подтвердила поддержку generateContent")
    print(f"Gemini OK · {model} · авторизация и поддержка generateContent подтверждены")


def check_mistral(pdf: Path, pages: list[int], review_output: Path | None) -> None:
    backend = MistralOCRBackend()
    if not backend.api_key:
        raise ProviderError("Mistral OCR: отсутствует MISTRAL_API_KEY")
    if review_output is not None and (review_output.suffix.lower() != ".json" or review_output.exists()):
        raise ValueError("Mistral OCR: укажите новый файл .json для проверки")
    result = backend.extract_document(pdf, pages=pages)
    if not result.markdown.strip():
        raise ProviderError("Mistral OCR: на выбранных страницах текст не распознан")
    if review_output is not None:
        with review_output.open("x", encoding="utf-8") as output:
            json.dump({
                "status": "manual_review",
                "source_file": pdf.name,
                "source_sha256": result.source_sha256,
                "model": result.model,
                "pages": result.pages,
            }, output, ensure_ascii=False, indent=2)
            output.write("\n")
    print(f"Mistral OCR OK · распознано страниц: {len(result.pages)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="service", required=True)
    finam = commands.add_parser("finam", help="Проверить режим только чтение и получить котировку")
    finam.add_argument("--symbol", default="SBER@MISX")
    commands.add_parser("gemini", help="Проверить доступ к метаданным модели Gemini")
    mistral = commands.add_parser("mistral", help="Распознать страницы выбранного PDF")
    mistral.add_argument("--pdf", required=True, type=Path)
    mistral.add_argument("--pages", nargs="+", type=int, default=[0], help="Номера страниц с нуля")
    mistral.add_argument("--review-output", type=Path, help="Новый локальный JSON для ручной проверки")
    args = parser.parse_args()

    try:
        if args.service == "finam":
            quote = latest_quote(os.environ.get("FINAM_TRADE_API_KEY", ""), args.symbol)
            print(f"Finam OK · {quote.symbol} · котировка от {quote.timestamp.isoformat()}")
        elif args.service == "gemini":
            check_gemini()
        else:
            check_mistral(args.pdf, args.pages, args.review_output)
    except (ProviderError, ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
