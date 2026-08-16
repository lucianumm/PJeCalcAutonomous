"""Especificação tipada do módulo oficial ``Cálculo Externo``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from .calculation_spec import Provenance, parse_br_decimal, parse_date


class Parcela(BaseModel):
    key: str = Field(..., min_length=1)
    ativa: bool = True
    principal: Optional[Decimal] = None
    juros: Optional[Decimal] = None
    indice: Optional[str] = None
    descricao: Optional[str] = None
    aliquota: Optional[Decimal] = None
    aplicar_juros: Optional[bool] = None
    data_juros_a_partir_de: Optional[date | str] = None
    grupo: Optional[str] = None
    # Alguns grupos do PJe-Calc 2.16.0 possuem dois campos de principal
    # (vencidas até fev/2009 e a partir de mar/2009). Os nomes aceitos aqui
    # são chaves semânticas resolvidas por selectors.py; o agente nunca aceita
    # ids DOM arbitrários vindos do usuário.
    componentes: Dict[str, Decimal] = Field(default_factory=dict)
    provenance: List[Provenance] = Field(default_factory=list)

    @field_validator("principal", "juros", "aliquota", mode="before")
    @classmethod
    def _decimal(cls, value: Any) -> Optional[Decimal]:
        return parse_br_decimal(value)

    @field_validator("data_juros_a_partir_de", mode="before")
    @classmethod
    def _date(cls, value: Any) -> Any:
        return parse_date(value) if value is not None else None

    @field_validator("componentes", mode="before")
    @classmethod
    def _componentes(cls, value: Any) -> Dict[str, Decimal]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError("componentes deve ser um objeto")
        result: Dict[str, Decimal] = {}
        for key, item in value.items():
            if not str(key).strip():
                raise ValueError("componente sem chave")
            parsed = parse_br_decimal(item)
            if parsed is None:
                raise ValueError(f"componente sem valor: {key}")
            result[str(key)] = parsed
        return result


class ExclusaoVerba(BaseModel):
    """Verba existente no PJC que deve ser retirada pela UI oficial.

    A exclusão é deliberadamente por descrição, com correspondência única.
    Isso evita apagar várias verbas por um filtro amplo e mantém a alteração
    posterior aos pagamentos dentro do modelo do PJe-Calc, que refaz o rateio
    ao liquidar.
    """

    descricao: str = Field(..., min_length=1)
    ocorrencias_esperadas: int = Field(default=1, ge=1, le=1)
    provenance: List[Provenance] = Field(default_factory=list)


class ExternalCalculationSpec(BaseModel):
    spec_version: str = "2.0"
    case_id: Optional[str] = None
    base_pjc_path: Optional[str] = None
    base_calculation_number: Optional[str] = None
    processo: Dict[str, Any] = Field(default_factory=dict)
    reclamante: Optional[str] = None
    reclamado: Optional[str] = None
    data_ultima_atualizacao: Optional[date | str] = None
    data_final_atualizacao: Optional[date | str] = None
    indice_trabalhista: Optional[str] = None
    combinacoes_indices: List[Parcela] = Field(default_factory=list)
    ignorar_taxa_negativa: Optional[bool] = None
    juros: Optional[str] = None
    combinacoes_juros: List[Parcela] = Field(default_factory=list)
    base_juros_verbas: Optional[str] = None
    fgts_destino: Optional[str] = None
    fgts_correcao: Optional[str] = None
    contribuicao_social_salarios_devidos: Optional[bool] = None
    contribuicao_social_salarios_pagos: Optional[bool] = None
    lei_11941: Optional[bool] = None
    irpf: Optional[bool] = None
    custas: Optional[bool] = None
    creditos_reclamante: List[Parcela] = Field(default_factory=list)
    descontos_reclamante: List[Parcela] = Field(default_factory=list)
    outros_debitos_reclamado: List[Parcela] = Field(default_factory=list)
    debitos_reclamante: List[Parcela] = Field(default_factory=list)
    excluir_verbas: List[ExclusaoVerba] = Field(default_factory=list)
    reclamado_remanescente: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    provenance: List[Provenance] = Field(default_factory=list)

    @field_validator("data_ultima_atualizacao", "data_final_atualizacao", mode="before")
    @classmethod
    def _dates(cls, value: Any) -> Any:
        return parse_date(value) if value is not None else None

    @property
    def target_date(self) -> Any:
        return self.data_final_atualizacao

    def required_fields(self) -> List[str]:
        missing: List[str] = []
        if self.data_ultima_atualizacao in (None, ""):
            missing.append("data_ultima_atualizacao")
        if self.data_final_atualizacao in (None, ""):
            missing.append("data_final_atualizacao")
        if self.indice_trabalhista in (None, ""):
            missing.append("indice_trabalhista")
        if not (
            self.creditos_reclamante or self.descontos_reclamante
            or self.outros_debitos_reclamado or self.debitos_reclamante
            or self.excluir_verbas or self.reclamado_remanescente
        ):
            missing.append("parcelas")
        for group_name in ("creditos_reclamante", "descontos_reclamante", "outros_debitos_reclamado", "debitos_reclamante"):
            for index, parcela in enumerate(getattr(self, group_name)):
                if parcela.ativa and parcela.principal is None and not parcela.componentes:
                    # A parcela checkbox-only (por exemplo, custas já
                    # calculadas no PJC) não possui campo de principal nesta
                    # tela e é válida sem valor informado. A resolução da
                    # chave e a exigência de valor, quando houver, ficam no
                    # mapa estático de seletores.
                    from .selectors import external_parcel_binding
                    binding = external_parcel_binding(group_name, parcela.key)
                    if binding is None or binding.get("value_required", True):
                        missing.append(f"{group_name}[{index}].principal")
                if parcela.ativa and parcela.aplicar_juros and parcela.juros is None:
                    missing.append(f"{group_name}[{index}].juros")
                from .selectors import external_parcel_binding
                if external_parcel_binding(group_name, parcela.key) is None:
                    missing.append(f"{group_name}[{index}].key desconhecida")
        seen: set[tuple[str, str]] = set()
        for group_name in ("creditos_reclamante", "descontos_reclamante", "outros_debitos_reclamado", "debitos_reclamante"):
            for index, parcela in enumerate(getattr(self, group_name)):
                key = (group_name, parcela.key)
                if key in seen:
                    missing.append(f"{group_name}[{index}].key duplicada")
                seen.add(key)
        return missing
