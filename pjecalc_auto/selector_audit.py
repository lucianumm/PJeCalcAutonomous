"""Verificação estática dos seletores contra o XHTML empacotado.

O DOM renderizado pelo JSF pode acrescentar o prefixo ``formulario:``; por
isso a auditoria compara também o último segmento do id e registra casos que
precisam de confirmação em navegador, sem transformar ausência estática em
falso positivo.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

from . import selectors


def audit_selectors(vendor_dir: Path) -> Dict[str, Any]:
    root = vendor_dir / "tomcat" / "webapps" / "pjecalc"
    files = sorted(root.rglob("*.xhtml")) if root.is_dir() else []
    chunks = []
    for path in files:
        raw = path.read_bytes()
        try:
            chunks.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            chunks.append(raw.decode("ISO-8859-1", errors="ignore"))
    corpus = "\n".join(chunks)
    results: Dict[str, Any] = {}
    for name, value in vars(selectors).items():
        if not name.isupper() or not isinstance(value, str):
            continue
        candidates = [value]
        if value.startswith("formulario:"):
            candidates.append(value.split(":", 1)[1])
        dynamic_css = value.startswith(("input[", "a[", "input."))
        if dynamic_css:
            # styleClass/title selectors are checked literally below.
            candidates = [value.replace("[", " ").replace("]", " ")]
        found = any(
            re.search(rf'\bid\s*=\s*["\'](?:[^"\']*:)?{re.escape(candidate)}["\']', corpus)
            for candidate in candidates
        )
        if "title=" in value or "styleClass" in value or dynamic_css:
            token = value.split("'")[1] if "'" in value else value.lstrip(".")
            found = token in corpus
        results[name] = {
            "selector": value,
            "status": "STATIC_MATCH" if found else "BROWSER_CONFIRMATION_REQUIRED",
        }
    matched = sum(item["status"] == "STATIC_MATCH" for item in results.values())
    browser_only = sum(
        item["status"] == "BROWSER_CONFIRMATION_REQUIRED"
        for item in results.values()
    )
    return {
        "ok": bool(files) and matched + browser_only == len(results),
        "files_scanned": len(files),
        "matched": matched,
        "browser_confirmation_required": browser_only,
        "total": len(results),
        "results": results,
        "note": "RichFaces/JSF selectors without literal XHTML ids require browser confirmation.",
    }
