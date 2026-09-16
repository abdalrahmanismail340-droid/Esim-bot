"""
Reads a screenshot of a Binance transfer using OCR.space's free API and pulls out
digit sequences that could plausibly be the transaction/order ID.

Get a free API key (no credit card needed) at: https://ocr.space/ocrapi/freekey
Free tier: 25,000 requests/month, 500/day.
"""

import logging
import re

import requests

log = logging.getLogger(__name__)

OCR_SPACE_URL = "https://api.ocr.space/parse/image"


def ocr_space_extract_text(api_key: str, image_bytes: bytes) -> str:
    """Sends the image to OCR.space and returns the raw recognized text (may be empty)."""
    try:
        resp = requests.post(
            OCR_SPACE_URL,
            data={
                "apikey": api_key,
                "language": "eng",
                "OCREngine": 2,
                "isOverlayRequired": False,
                "scale": True,
            },
            files={"file": ("screenshot.jpg", image_bytes, "image/jpeg")},
            timeout=30,
        )
        data = resp.json()
    except Exception as e:
        log.warning(f"OCR.space request failed: {e}")
        return ""

    if data.get("IsErroredOnProcessing"):
        log.warning(f"OCR.space returned an error: {data.get('ErrorMessage')}")
        return ""

    results = data.get("ParsedResults") or []
    if not results:
        return ""
    return results[0].get("ParsedText", "") or ""


def extract_candidate_ids(text: str) -> list:
    """Pulls out digit sequences (6-25 digits) that could plausibly be a
    Binance transaction/order ID, longest first (usually the most specific)."""
    candidates = re.findall(r"\d{6,25}", text)
    seen = set()
    ordered = []
    for c in sorted(candidates, key=len, reverse=True):
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def extract_tx_ids_from_image(api_key: str, image_bytes: bytes) -> list:
    """Convenience wrapper: OCR the image, then return candidate ID strings."""
    text = ocr_space_extract_text(api_key, image_bytes)
    if not text:
        return []
    return extract_candidate_ids(text)
