"""Modos de liquidação e detecção documental automática.

O PJe-Calc 2.16.0 tem três caminhos reais de operação:

- STANDARD: reconstruir a condenação pela origem (verbas/reflexos/FGTS/INSS/
  IRPF/multas/honorários/custas/pagamentos) e liquidar via `Calculo.liquidar()`.
- EXTERNAL_UPDATE: já existe liquidação judicial anterior; atualizar o saldo
  pelo módulo oficial "Cálculo Externo" (telas `calculo-externo.xhtml` +
  `parcelas-atualizaveis.xhtml`). Nenhuma conta é feita fora do PJe-Calc.
- IMPORT_PJC: existe arquivo `.PJC` transportável; importar e reorganizar.

A detecção é documental e explícita; a decisão é registrada com `reason`,
`source` e `confidence`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CalculationMode(str, Enum):
    STANDARD = "STANDARD"
    EXTERNAL_UPDATE = "EXTERNAL_UPDATE"
    IMPORT_PJC = "IMPORT_PJC"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass
class ModeDecision:
    mode: CalculationMode
    reason: str
    source: str
    confidence: float  # 0.0 .. 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "reason": self.reason,
            "source": self.source,
            "confidence": self.confidence,
        }


def resolve_calculation_mode(
    pjc_available: bool = False,
    has_prior_liquidation: bool = False,
    has_update_plan: bool = False,
    source: str = "processo",
) -> ModeDecision:
    """Resolve o modo a partir de indícios documentais.

    Preferências (ordem):
      1. IMPORT_PJC, se houver `.PJC` disponível (transporte oficial).
      2. EXTERNAL_UPDATE, se houver liquidação anterior e o objetivo for
         atualizar saldo (ex.: planilha de atualização de cálculo oficial).
      3. STANDARD, caso contrário.
    """
    if has_prior_liquidation and has_update_plan and pjc_available:
        return ModeDecision(
            mode=CalculationMode.EXTERNAL_UPDATE,
            reason="official_pjc_available_for_existing_liquidation_update",
            source=source,
            confidence=1.0,
        )
    if pjc_available:
        return ModeDecision(
            mode=CalculationMode.IMPORT_PJC,
            reason="existing_official_pjc_transport_available",
            source=source,
            confidence=1.0,
        )
    if has_prior_liquidation and has_update_plan:
        return ModeDecision(
            mode=CalculationMode.EXTERNAL_UPDATE,
            reason="existing_official_liquidation_with_explicit_update_plan",
            source=source,
            confidence=0.9,
        )
    if has_prior_liquidation and not has_update_plan:
        return ModeDecision(
            mode=CalculationMode.RECONCILIATION_REQUIRED,
            reason="prior_liquidation_without_explicit_update_plan",
            source=source,
            confidence=1.0,
        )
    return ModeDecision(
        mode=CalculationMode.STANDARD,
        reason="no_prior_liquidation_or_pjc",
        source=source,
        confidence=1.0,
        )


@dataclass
class ModeCompatibilityInput:
    pjc_valid: bool = False
    has_prior_liquidation: bool = False
    title_changed_after_liquidation: bool = False
    changed_component_paid: bool = False
    external_can_represent_change: bool = False
    requires_event_reconstruction: bool = False
    standard_data_complete: bool = False
    pjc_can_be_imported_and_edited: bool = False
    source: str = "processo"


class ModeCompatibilityAnalyzer:
    """Decide o modo sem tratar mera existência de arquivo como prova."""

    def decide(self, data: ModeCompatibilityInput) -> ModeDecision:
        if data.pjc_valid and data.pjc_can_be_imported_and_edited:
            return ModeDecision(CalculationMode.IMPORT_PJC, "valid_pjc_compatible", data.source, 1.0)
        if data.title_changed_after_liquidation and data.changed_component_paid:
            return ModeDecision(
                CalculationMode.RECONCILIATION_REQUIRED,
                "retroactive_title_change_affects_paid_component",
                data.source,
                1.0,
            )
        if data.has_prior_liquidation and data.external_can_represent_change and not data.requires_event_reconstruction:
            return ModeDecision(CalculationMode.EXTERNAL_UPDATE, "external_update_represents_state", data.source, 0.9)
        if data.standard_data_complete:
            return ModeDecision(CalculationMode.STANDARD, "standard_inputs_complete", data.source, 0.9)
        return ModeDecision(CalculationMode.FAIL_CLOSED, "insufficient_mode_evidence", data.source, 1.0)
