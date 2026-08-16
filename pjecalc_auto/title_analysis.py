"""Análise conservadora da evolução do título executivo."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from .calculation_spec import JudicialAdjustment, Provenance, SourceStatus


@dataclass
class TitleDocument:
    kind: str
    source: str
    page: Optional[int] = None
    authority: Optional[str] = None
    date: Optional[str] = None
    text: str = ""


@dataclass
class TitleResolution:
    timeline: List[TitleDocument] = field(default_factory=list)
    adjustments: List[JudicialAdjustment] = field(default_factory=list)
    conflicts: List[dict] = field(default_factory=list)
    superseded: List[dict] = field(default_factory=list)
    status: str = "UNRESOLVED"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "timeline": [doc.__dict__ for doc in self.timeline],
            "adjustments": [item.model_dump(mode="json") for item in self.adjustments],
            "conflicts": self.conflicts,
            "superseded": self.superseded,
        }


_KINDS = (
    ("sentenca", re.compile(r"\bsenten[çc]a\b", re.I)),
    ("embargos", re.compile(r"\bembargos?\b", re.I)),
    ("recurso_ordinario", re.compile(r"recurso\s+ordin[aá]rio", re.I)),
    ("recurso_revista", re.compile(r"recurso\s+de\s+revista", re.I)),
    ("agravo", re.compile(r"\bagravo\b", re.I)),
    ("decisao_posterior", re.compile(r"decis[aã]o\s+posterior", re.I)),
    ("transito_julgado", re.compile(r"tr[aâ]nsito\s+em\s+julgado", re.I)),
)


def analyze_title(documents: Iterable[TitleDocument]) -> TitleResolution:
    resolution = TitleResolution(timeline=list(documents))
    for doc in resolution.timeline:
        for kind, pattern in _KINDS:
            if pattern.search(doc.text):
                doc.kind = kind
                break
        resolution.adjustments.extend(_extract_adjustments(doc))
    resolution.timeline.sort(key=lambda item: (item.date or "", item.source))
    _detect_conflicts(resolution)
    resolution.status = "PASS" if resolution.adjustments and not resolution.conflicts else "UNRESOLVED"
    return resolution


def _extract_adjustments(doc: TitleDocument) -> list[JudicialAdjustment]:
    text = doc.text
    adjustments: list[JudicialAdjustment] = []
    patterns = [
        ("EXCLUDE", r"(?:afastad[ao]|exclu[íi]d[ao]|n[aã]o incide|retirad[ao]).{0,120}(multa|verba|parcela)[^.;\n]*"),
        ("INCLUDE", r"(?:deferid[ao]|inclu[íi]d[ao]|reconhecid[ao]).{0,120}(multa|verba|parcela)[^.;\n]*"),
        ("MAINTAIN", r"(?:mantid[ao]|mant[eé]m-se|preservad[ao]).{0,120}(custas|verba|parcela)[^.;\n]*"),
    ]
    for action, pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            target = match.group(1).upper()
            description = match.group(0).strip()[:500]
            adjustments.append(JudicialAdjustment(
                action=action, target=target, description=description,
                source=doc.source, page=doc.page, authority=doc.authority,
                effective_date=doc.date, confidence=0.8,
                provenance=[Provenance(source=doc.source,
                                       status=SourceStatus.JUDICIAL_DETERMINATION,
                                       page=doc.page, excerpt=description,
                                       authority=doc.authority,
                                       confidence=0.8)],
            ))
    return adjustments


def _detect_conflicts(resolution: TitleResolution) -> None:
    by_target: dict[str, list[JudicialAdjustment]] = {}
    for adjustment in resolution.adjustments:
        by_target.setdefault(adjustment.target, []).append(adjustment)
    for target, items in by_target.items():
        actions = {item.action for item in items}
        if "EXCLUDE" in actions and "INCLUDE" in actions:
            resolution.conflicts.append({
                "target": target,
                "adjustments": [item.model_dump(mode="json") for item in items],
                "reason": "fontes determinam ações incompatíveis",
            })
