"""Parser tolerante para relatórios oficiais de liquidação do PJe-Calc.

Valores extraídos permanecem documentados com proveniência; o parser não faz
rateio nem recalcula saldos.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .calculation_spec import PaymentEvent, Provenance, parse_br_decimal, parse_date


class PriorField(BaseModel):
    value: Any = None
    provenance: List[Provenance] = Field(default_factory=list)


class PriorLiquidationSpec(BaseModel):
    calculation_number: Optional[PriorField] = None
    process_number: Optional[PriorField] = None
    reclamante: Optional[PriorField] = None
    reclamado: Optional[PriorField] = None
    period: Optional[PriorField] = None
    filing_date: Optional[PriorField] = None
    liquidation_date: Optional[PriorField] = None
    pjecalc_version: Optional[PriorField] = None
    criteria: Dict[str, PriorField] = Field(default_factory=dict)
    totals: Dict[str, PriorField] = Field(default_factory=dict)
    payments: List[PaymentEvent] = Field(default_factory=list)
    events: List[Dict[str, Any]] = Field(default_factory=list)
    provenance: List[Provenance] = Field(default_factory=list)

    @property
    def strong_signature(self) -> bool:
        """Indício mínimo para distinguir uma liquidação de um processo comum."""
        return bool(
            self.calculation_number and self.liquidation_date
            and (self.totals or self.payments)
        )


_LABELS = {
    "calculation_number": (r"c[aá]lculo\s*(?:n[ºo°.]*)?\s*([0-9]+)", "Número do cálculo"),
    "process_number": (r"processo\s*(?:n[ºo°.]*)?\s*([0-9.\-]+)", "Número do processo"),
    "liquidation_date": (r"(?:data\s+de\s+liquida[çc][aã]o|(?:elaborad[ao]|atualiza[çc][aã]o)\s+em)\s*[:\-]?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})", "Data de liquidação/atualização"),
    "filing_date": (r"ajuizamento\s*[:\-]?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})", "Ajuizamento"),
    "pjecalc_version": (r"PJe-?Calc\s*[:v.]?\s*([0-9]+\.[0-9]+\.[0-9]+)", "Versão PJe-Calc"),
}
_MONEY = {
    "principal": r"principal\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "juros": r"juros(?!\s+FGTS)\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "fgts": r"FGTS(?!\s+juros|\s+multa)\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "juros_fgts": r"juros\s+FGTS\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "multa_fgts": r"multa\s+FGTS\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "contribuicao_social": r"contribui[çc][aã]o\s+social\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "irpf": r"IRPF\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "honorarios": r"honor[aá]rios\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "custas": r"custas\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "liquido": r"l[ií]quido(?:\s+reclamante)?\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "total_reclamado": r"total\s+do\s+reclamado\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "total_devido_reclamado": r"total\s+devido\s+pelo\s+reclamado\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "liquido_devido_reclamante": r"l[ií]quido\s+devido\s+ao\s+reclamante\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "irpf_devido_reclamante": r"IRPF\s+devido\s+pel[ao]\s+reclamante\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
    "custas_devidas_reclamado": r"custas\s+devidas\s+pelo\s+reclamado\s*[:\-]?\s*R?\$?\s*([0-9.,]+)",
}


def parse_prior_liquidation(path: Path) -> PriorLiquidationSpec:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace") if path.suffix.lower() != ".pdf" else _extract_pdf(path)
    file_hash = _sha256(path)
    result = PriorLiquidationSpec()
    for field, (pattern, label) in _LABELS.items():
        match = re.search(pattern, text, re.I)
        if match:
            value: Any = match.group(1)
            if "date" in field:
                value = parse_date(value)
            result.__setattr__(field, _field(value, path, file_hash, label, match.group(0)))
    for field, pattern in _MONEY.items():
        match = re.search(pattern, text, re.I)
        if match:
            result.totals[field] = _field(parse_br_decimal(match.group(1)), path, file_hash, field, match.group(0))
    payment_patterns = (
        r"pagamento\s+(?:em\s+)?([0-9]{1,2}/[0-9]{1,2}/[0-9]{4}).{0,80}?R?\$?\s*([0-9.,]+)",
        r"pagamentos?\s+de\s+R?\$?\s*([0-9.,]+)\s+em\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
        r"(?:\be|,)\s+R?\$?\s*([0-9.,]+)\s+em\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
    )
    for pattern_index, pattern in enumerate(payment_patterns):
        for match in re.finditer(pattern, text, re.I):
            date_raw, amount_raw = match.groups()
            if pattern_index in {1, 2}:
                amount_raw, date_raw = date_raw, amount_raw
            result.payments.append(PaymentEvent(
                date=parse_date(date_raw), amount=parse_br_decimal(amount_raw) or Decimal("0"),
                type="PAYMENT", source=str(path),
                provenance=[Provenance(source=str(path), status="DOCUMENTED",
                                       file_sha256=file_hash, excerpt=match.group(0)[:300])],
            ))
    unique_payments: list[PaymentEvent] = []
    seen_payments: set[tuple[Any, Decimal]] = set()
    for payment in result.payments:
        marker = (payment.date, payment.amount)
        if marker not in seen_payments:
            unique_payments.append(payment)
            seen_payments.add(marker)
    result.payments = unique_payments
    # Registra possíveis rubricas que precisam ser retiradas por ação oficial
    # quando uma decisão posterior as excluiu. O parser não altera a conta.
    for match in re.finditer(
        r"(?:multa|verba)\s+embargos?\s+de\s+declara[çc][aã]o[^\n]{0,100}",
        text, re.I,
    ):
        result.events.append({
            "type": "POSSIBLE_POST_LIQUIDATION_EXCLUSION",
            "description": match.group(0).strip(),
            "source": str(path),
        })
    result.provenance.append(Provenance(source=str(path), status="DOCUMENTED",
                                        file_sha256=file_hash, excerpt="relatório oficial"))
    return result


def _field(value: Any, path: Path, digest: str, label: str, excerpt: str) -> PriorField:
    return PriorField(value=value, provenance=[Provenance(source=str(path),
        status="DERIVED", document_id=label, file_sha256=digest,
        excerpt=excerpt, confidence=0.9)])


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    except ImportError as exc:
        raise RuntimeError("pypdf necessário para relatórios PDF") from exc


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
