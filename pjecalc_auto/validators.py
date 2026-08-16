"""Validadores fail-closed por modo e de artefatos oficiais."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

from .calculation_spec import CalculationSpec
from .external_spec import ExternalCalculationSpec


MAX_PJC_BYTES = 100 * 1024 * 1024
MAX_PJC_ENTRIES = 2_000
MAX_PJC_UNCOMPRESSED = 250 * 1024 * 1024
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 2_000


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "details": self.details,
        }


class StandardSpecValidator:
    def validate(self, spec: CalculationSpec) -> ValidationResult:
        errors = spec.critical_parameters(mode="STANDARD")
        return ValidationResult(not errors, errors=errors)


class ExternalSpecValidator:
    def validate(self, spec: ExternalCalculationSpec) -> ValidationResult:
        errors = spec.required_fields()
        return ValidationResult(not errors, errors=errors)


class ImportPjcValidator:
    """Valida estrutura mínima de um PJC produzido pelo PJe-Calc.

    A validação não importa nem modifica o arquivo e não tenta recalcular os
    valores. Identificadores são extraídos do XML para posterior confirmação na
    UI oficial.
    """

    _VERSION_RE = re.compile(r"(?:vers[aã]o|version)[^0-9]*(2\.\d+(?:\.\d+)?)", re.I)

    def validate(self, path: Path, *, target_date: Optional[str] = None) -> ValidationResult:
        path = Path(path).expanduser().resolve()
        errors: List[str] = []
        details: Dict[str, Any] = {"path": str(path)}
        if not path.is_file():
            return ValidationResult(False, ["PJC_NOT_FOUND"], details=details)
        if path.stat().st_size > MAX_PJC_BYTES:
            return ValidationResult(False, ["PJC_SIZE_LIMIT"], details=details)
        if path.suffix.lower() not in {".pjc", ".zip"}:
            errors.append("PJC_EXTENSION_INVALID")
        try:
            with zipfile.ZipFile(path) as archive:
                entries = [name for name in archive.namelist() if not name.endswith("/")]
                if len(entries) > MAX_PJC_ENTRIES:
                    errors.append("PJC_ENTRY_COUNT_LIMIT")
                if any(Path(name).is_absolute() or ".." in Path(name).parts
                       for name in archive.namelist()):
                    errors.append("PJC_PATH_TRAVERSAL_ENTRY")
                if sum(info.file_size for info in archive.infolist()) > MAX_PJC_UNCOMPRESSED:
                    errors.append("PJC_UNCOMPRESSED_SIZE_LIMIT")
                bad = archive.testzip() if not errors else None
                if bad:
                    errors.append(f"PJC_CRC_INVALID:{bad}")
                xml_entries = [name for name in entries if name.lower().endswith(".xml")]
                if len(xml_entries) != 1:
                    errors.append("PJC_XML_ENTRY_INVALID")
                if xml_entries:
                    raw = archive.read(xml_entries[0])
                    if len(raw) > 50 * 1024 * 1024:
                        errors.append("PJC_XML_SIZE_LIMIT")
                        raw = b""
                    try:
                        text = raw.decode("iso-8859-1")
                    except UnicodeDecodeError:
                        text = raw.decode("utf-8", errors="strict")
                    try:
                        root = ElementTree.fromstring(text)
                    except ElementTree.ParseError as exc:
                        errors.append(f"PJC_XML_INVALID:{exc}")
                    else:
                        details["xml_root"] = root.tag
                        details["xml_entry"] = xml_entries[0]
                        details["xml_sha256"] = _sha256_bytes(raw)
                        details["version"] = _find_version(text)
                        details["identifiers"] = _find_identifiers(text)
                        if not details["identifiers"]:
                            errors.append("PJC_IDENTIFIERS_MISSING")
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            errors.append(f"PJC_ZIP_INVALID:{exc}")
        if target_date is not None:
            details["target_date"] = target_date
        return ValidationResult(not errors, errors=errors, details=details)


def validate_pdf(path: Path, *, expected_text: Optional[List[str]] = None) -> ValidationResult:
    path = Path(path).expanduser().resolve()
    errors: List[str] = []
    details: Dict[str, Any] = {"path": str(path)}
    if not path.is_file():
        return ValidationResult(False, ["PDF_NOT_FOUND"], details=details)
    raw = path.read_bytes()
    if len(raw) > MAX_PDF_BYTES:
        return ValidationResult(False, ["PDF_SIZE_LIMIT"], details=details)
    if not raw.startswith(b"%PDF"):
        errors.append("PDF_HEADER_INVALID")
    expected_text = expected_text or []
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        details["pages"] = len(reader.pages)
        if len(reader.pages) > MAX_PDF_PAGES:
            errors.append("PDF_PAGE_COUNT_LIMIT")
            return ValidationResult(False, errors=errors, details=details)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        details["text_excerpt"] = text[:2000]
        for token in expected_text:
            if token and token not in text:
                errors.append(f"PDF_EXPECTED_TEXT_MISSING:{token}")
    except ImportError:
        errors.append("PDF_READER_NOT_INSTALLED")
    except Exception as exc:
        errors.append(f"PDF_PARSE_FAILED:{exc}")
    return ValidationResult(not errors, errors=errors, details=details)


def _sha256_bytes(raw: bytes) -> str:
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def _find_version(text: str) -> Optional[str]:
    match = ImportPjcValidator._VERSION_RE.search(text)
    return match.group(1) if match else None


def _find_identifiers(text: str) -> dict:
    identifiers: dict = {}
    for key in (
        "numeroProcesso", "numero", "processo", "idCalculo", "numeroCalculo",
        "codigoCalculo", "calculoNumero", "calculo",
    ):
        match = re.search(rf"{re.escape(key)}\s*=\s*['\"]([^'\"]+)", text, re.I)
        if match:
            identifiers[key] = match.group(1)
        # O exportador do PJe-Calc alterna entre atributos e elementos,
        # dependendo da entidade. Ler somente atributos fazia a checagem do
        # cálculo-base rejeitar PJC válidos sem permitir um fallback inseguro.
        element = re.search(
            rf"<[^>]*\b{re.escape(key)}\b[^>]*>\s*([^<]+?)\s*</[^>]+>",
            text,
            re.I,
        )
        if element:
            identifiers.setdefault(key, element.group(1).strip())
    return identifiers
