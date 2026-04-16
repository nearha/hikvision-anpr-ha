from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def xml_to_obj(elem: ET.Element) -> Any:
    children = list(elem)
    text = (elem.text or "").strip()
    attrs = {f"@{strip_ns(k)}": v for k, v in elem.attrib.items()}
    if not children and not attrs:
        return text
    result: dict[str, Any] = {}
    if attrs:
        result.update(attrs)
    grouped: dict[str, list[Any]] = {}
    for child in children:
        grouped.setdefault(strip_ns(child.tag), []).append(xml_to_obj(child))
    for key, values in grouped.items():
        result[key] = values[0] if len(values) == 1 else values
    if text:
        result["#text"] = text
    return result


def parse_xml_bytes(payload: bytes) -> dict[str, Any]:
    root = ET.fromstring(payload)
    return {strip_ns(root.tag): xml_to_obj(root)}


def parse_header_lines(blob: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_line in blob.split(b"\r\n"):
        if not raw_line or b":" not in raw_line:
            continue
        key, value = raw_line.split(b":", 1)
        headers[key.decode("utf-8", errors="replace").strip().lower()] = value.decode(
            "utf-8", errors="replace"
        ).strip()
    return headers


def parse_content_disposition(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (value or "").split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        out[key.strip().lower()] = val.strip().strip('"')
    return out


def extract_boundary(content_type: str | None) -> bytes | None:
    match = re.search(r'boundary="?([^";]+)"?', content_type or "", re.IGNORECASE)
    if not match:
        return None
    value = match.group(1)
    if value.startswith("--"):
        value = value[2:]
    return value.encode("utf-8")


def sanitize_filename(value: str, max_len: int = 180) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    value = value.strip("._-") or "item"
    return value[:max_len]


def guess_extension(content_type: str, fallback: str = ".bin") -> str:
    content_type = (content_type or "").lower()
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "xml" in content_type:
        return ".xml"
    return fallback


def content_name(headers: dict[str, str]) -> tuple[str, str]:
    content_disposition = parse_content_disposition(headers.get("content-disposition", ""))
    filename = content_disposition.get("filename") or ""
    name = content_disposition.get("name") or ""
    stem = Path(filename or name).stem
    return filename, stem
