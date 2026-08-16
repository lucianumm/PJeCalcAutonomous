"""ProcessCorpus — corpus estruturado derivado do AuditorProcessual.

É o elo entre a ingestão processual e o CalculationSpec. Representa, de forma
auditável, os dados factuais extraídos do processo (sem inventar valores) que
serão usados para construir a especificação de cálculo.

Estrutura mínima derivada do process_manifest.schema.json do AuditorProcessual
+ fatos relevantes para cálculo trabalhista (datas, valores, premissas).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .calculation_spec import SourceStatus


class Fact(BaseModel):
    """Um fato extraído do processo, com âncora de página/peça."""

    category: str
    value: Any
    piece: Optional[str] = None
    pdf_page: Optional[int] = None
    court_page: Optional[str] = None
    excerpt: Optional[str] = None
    document_id: Optional[str] = None
    file_sha256: Optional[str] = None
    authority: Optional[str] = None
    document_date: Optional[str] = None
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    status: SourceStatus = SourceStatus.EXPLICIT
    note: Optional[str] = None


class ProcessCorpus(BaseModel):
    """Corpus estruturado e rastreável do processo."""

    process_id: str
    manifest: Dict[str, Any] = Field(default_factory=dict)
    facts: List[Fact] = Field(default_factory=list)

    def facts_by_category(self, category: str) -> List[Fact]:
        return [f for f in self.facts if f.category == category]
