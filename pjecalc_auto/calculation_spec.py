"""Modelos tipados, proveniência e validação fail-closed.

O módulo transporta parâmetros para a UI oficial. Ele não implementa nenhuma
regra de liquidação do PJe-Calc.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def parse_br_decimal(value: Any) -> Optional[Decimal]:
    """Converte `1.234,56`/`1234.56` sem usar float."""

    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("booleano não é valor financeiro")
    if isinstance(value, int):
        return Decimal(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"valor decimal inválido: {value!r}") from exc


def format_br_decimal(value: Any, places: int = 2) -> str:
    decimal = parse_br_decimal(value)
    if decimal is None:
        return ""
    quant = Decimal(1).scaleb(-places)
    rendered = decimal.quantize(quant, rounding=ROUND_HALF_EVEN)
    return f"{rendered:,.{places}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"data inválida: {value!r}")


def format_ui_date(value: Any) -> str:
    parsed = parse_date(value)
    return parsed.strftime("%d/%m/%Y") if parsed else ""


class SourceStatus(str, Enum):
    EXPLICIT = "EXPLICIT"
    EXTRACTED = "EXTRACTED"
    DOCUMENTED = "DOCUMENTED"
    DERIVED = "DERIVED"
    JUDICIAL_DETERMINATION = "JUDICIAL_DETERMINATION"
    ASSUMED = "ASSUMED"
    UNRESOLVED = "UNRESOLVED"
    CONFLICTING = "CONFLICTING"


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(..., description="Documento/artefato de origem")
    status: SourceStatus = SourceStatus.DOCUMENTED
    document_id: Optional[str] = None
    file_sha256: Optional[str] = None
    page: Optional[int] = None
    page_pdf: Optional[int] = None
    page_processual: Optional[str] = None
    excerpt: Optional[str] = None
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    authority: Optional[str] = None
    document_date: Optional[date] = None


class ResolvedValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any = None
    status: SourceStatus = SourceStatus.UNRESOLVED
    provenance: List[Provenance] = Field(default_factory=list)
    note: Optional[str] = None

    @property
    def unresolved(self) -> bool:
        return self.value is None or self.status in {
            SourceStatus.UNRESOLVED, SourceStatus.CONFLICTING,
        }


class ProcessSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    numero: Optional[str] = None
    digito: Optional[str] = None
    ano: Optional[str] = None
    justica: Optional[str] = None
    tribunal: Optional[str] = None
    regiao: Optional[str] = None
    vara: Optional[str] = None
    estado: Optional[str] = None
    municipio: Optional[str] = None
    reclamante: Optional[str] = None
    reclamado: Optional[str] = None
    data_ajuizamento: Optional[date] = None
    data_inicio_calculo: Optional[date] = None
    data_termino_calculo: Optional[date] = None
    data_liquidacao_atualizacao: Optional[date] = None


class ContractSpec(BaseModel):
    admissao: Optional[ResolvedValue] = None
    demissao: Optional[ResolvedValue] = None
    salario: Optional[ResolvedValue] = None
    jornada: Optional[ResolvedValue] = None
    cargo: Optional[ResolvedValue] = None
    regime: Optional[ResolvedValue] = None
    maior_remuneracao: Optional[ResolvedValue] = None
    ultima_remuneracao: Optional[ResolvedValue] = None
    aviso_previo: Optional[ResolvedValue] = None
    projecao_aviso_previo: Optional[ResolvedValue] = None


class SalaryHistorySpec(BaseModel):
    competencia: date | str
    valor: Decimal
    tipo_variacao: Optional[str] = None
    incidencias: Dict[str, bool] = Field(default_factory=dict)
    provenance: List[Provenance] = Field(default_factory=list)

    @field_validator("valor", mode="before")
    @classmethod
    def _decimal(cls, value: Any) -> Decimal:
        parsed = parse_br_decimal(value)
        if parsed is None:
            raise ValueError("valor salarial ausente")
        return parsed


class ReflexSpec(BaseModel):
    nome: Optional[str] = None
    principal: Optional[str] = None
    base: Optional[ResolvedValue] = None
    percentual: Optional[ResolvedValue] = None
    periodo_proprio: bool = False
    incidencias: Dict[str, bool] = Field(default_factory=dict)
    provenance: List[Provenance] = Field(default_factory=list)


class FgtsSpec(BaseModel):
    destino: Optional[str] = None
    incidencias: Dict[str, bool] = Field(default_factory=dict)
    multa: Optional[ResolvedValue] = None
    base: Optional[ResolvedValue] = None
    correcao: Optional[str] = None
    periodo: Optional["CalculationPeriod"] = None


class InssSpec(BaseModel):
    apurar: Optional[bool] = None
    empregado: Optional[bool] = None
    empregador: Optional[bool] = None
    terceiros: Optional[bool] = None
    sat_rat: Optional[bool] = None
    lei_11941: Optional[bool] = None
    salarios_devidos: Optional[ResolvedValue] = None
    salarios_pagos: Optional[ResolvedValue] = None


class IrpfSpec(BaseModel):
    apurar: Optional[bool] = None
    rra: Optional[bool] = None
    quantidade_meses: Optional[ResolvedValue] = None
    deducoes: List[ResolvedValue] = Field(default_factory=list)
    contribuicao_social: Optional[bool] = None
    honorarios: Optional[bool] = None
    dependentes: Optional[ResolvedValue] = None
    juros: Optional[bool] = None
    tipo_tributacao: Optional[str] = None


class CostSpec(BaseModel):
    tipo: Optional[str] = None
    valor: Optional[ResolvedValue] = None
    base: Optional[ResolvedValue] = None
    incidencia: Optional[str] = None
    preservada_por_decisao: Optional[bool] = None


class FeeSpec(BaseModel):
    beneficiario: Optional[str] = None
    descricao: Optional[str] = None
    tipo: Optional[str] = None
    valor: Optional[ResolvedValue] = None
    percentual: Optional[ResolvedValue] = None
    base: Optional[ResolvedValue] = None
    juros: Optional[bool] = None
    ir: Optional[bool] = None
    payments: List["PaymentEvent"] = Field(default_factory=list)


class FineSpec(BaseModel):
    descricao: Optional[str] = None
    valor: Optional[ResolvedValue] = None
    base: Optional[ResolvedValue] = None
    action: Optional[str] = None
    provenance: List[Provenance] = Field(default_factory=list)


class PaymentEvent(BaseModel):
    date: date | str
    amount: Decimal
    allocation: Optional[Dict[str, Decimal]] = None
    type: str = "PAYMENT"
    beneficiary: Optional[str] = None
    source: Optional[str] = None
    provenance: List[Provenance] = Field(default_factory=list)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, value: Any) -> Decimal:
        parsed = parse_br_decimal(value)
        if parsed is None:
            raise ValueError("pagamento sem valor")
        return parsed


class JudicialAdjustment(BaseModel):
    action: str
    target: str
    description: str
    source: Optional[str] = None
    page: Optional[int] = None
    authority: Optional[str] = None
    effective_date: Optional[date | str] = None
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    provenance: List[Provenance] = Field(default_factory=list)


class VacationSpec(BaseModel):
    periodo: Optional["CalculationPeriod"] = None
    dias: Optional[ResolvedValue] = None
    dobra: Optional[bool] = None
    provenance: List[Provenance] = Field(default_factory=list)


class AbsenceSpec(BaseModel):
    periodo: Optional["CalculationPeriod"] = None
    dias: Optional[ResolvedValue] = None
    tipo: Optional[str] = None
    provenance: List[Provenance] = Field(default_factory=list)


class TimecardSpec(BaseModel):
    periodo: Optional["CalculationPeriod"] = None
    jornada: Optional[ResolvedValue] = None
    arquivo: Optional[str] = None
    provenance: List[Provenance] = Field(default_factory=list)


class UpdateParameters(BaseModel):
    data_ultima_atualizacao: Optional[date | str] = None
    data_alvo: Optional[date | str] = None
    indice: Optional[str] = None
    juros: Optional[str] = None
    criterios: Dict[str, Any] = Field(default_factory=dict)


class CalculationPeriod(BaseModel):
    inicio: date | str
    fim: date | str


class VerbaSpec(BaseModel):
    """Parâmetros da tela oficial de verba, com campos opcionais por tipo."""

    tipo: str
    codigo: Optional[int] = None
    descricao: Optional[str] = None
    assunto_cnj: Optional[str] = None
    codigo_assuntos_cnj: Optional[str] = None
    base_calculo: Optional[str] = None
    tipo_base: Optional[str] = None
    tipo_variacao_da_parcela: Optional[str] = None
    valor: Optional[ResolvedValue] = None
    caracteristica: Optional[str] = None
    ocorrencia_pagto: Optional[str] = None
    ocorrencia_ajuizamento: Optional[str] = None
    juros_sumula_439: Optional[bool] = None
    gera_reflexo: Optional[bool] = None
    gerar_principal: Optional[bool] = None
    compor_principal: Optional[bool] = None
    zera_valor_negativo: Optional[bool] = None
    periodo_inicial: Optional[ResolvedValue] = None
    periodo_final: Optional[ResolvedValue] = None
    exclusoes: List[str] = Field(default_factory=list)
    dobra: bool = False
    base: Optional[ResolvedValue] = None
    bases_compostas: List[str] = Field(default_factory=list)
    divisor: Optional[ResolvedValue] = None
    tipo_divisor: Optional[str] = None
    multiplicador: Optional[ResolvedValue] = None
    percentual: Optional[ResolvedValue] = None
    quantidade: Optional[ResolvedValue] = None
    tipo_quantidade: Optional[str] = None
    valor_informado_quantidade: Optional[ResolvedValue] = None
    proporcionalidade: Optional[ResolvedValue] = None
    valor_pago: Optional[ResolvedValue] = None
    incidencias: Dict[str, bool] = Field(default_factory=dict)
    reflexos: List[ReflexSpec] = Field(default_factory=list)
    comentarios: Optional[str] = None
    indices: List[ResolvedValue] = Field(default_factory=list)


class CalculationSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    spec_version: str = "2.0"
    case_id: Optional[str] = None
    contract: ContractSpec = Field(default_factory=ContractSpec)
    processo: Dict[str, Any] = Field(default_factory=dict)
    process_provenance: Dict[str, List[Provenance]] = Field(default_factory=dict)
    process: Optional[ProcessSpec] = None
    verbas: List[VerbaSpec] = Field(default_factory=list)
    reflexos: List[ReflexSpec] = Field(default_factory=list)
    salary_history: List[SalaryHistorySpec] = Field(default_factory=list)
    fgts: Optional[FgtsSpec] = None
    inss: Optional[InssSpec] = None
    irpf: Optional[IrpfSpec] = None
    costs: List[CostSpec] = Field(default_factory=list)
    fees: List[FeeSpec] = Field(default_factory=list)
    fines: List[FineSpec] = Field(default_factory=list)
    payments: List[PaymentEvent] = Field(default_factory=list)
    judicial_adjustments: List[JudicialAdjustment] = Field(default_factory=list)
    vacations: List[VacationSpec] = Field(default_factory=list)
    absences: List[AbsenceSpec] = Field(default_factory=list)
    timecards: List[TimecardSpec] = Field(default_factory=list)
    update_parameters: Optional[UpdateParameters] = None
    calculation_period: Optional[CalculationPeriod] = None
    strict_mode: bool = True
    notes: List[str] = Field(default_factory=list)
    source_input_hashes: Dict[str, str] = Field(default_factory=dict)
    unsupported_facts: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)

    def critical_parameters(self, mode: str = "STANDARD") -> List[str]:
        """Retorna todos os requisitos críticos ausentes/conflitantes."""

        if mode == "EXTERNAL_UPDATE":
            return []
        problems: List[str] = []
        if mode in {"IMPORT_PJC", "RECONCILIATION_REQUIRED"}:
            return problems
        problems.extend(f"unsupported.{item}" for item in self.unsupported_facts)
        problems.extend(f"conflict.{item}" for item in self.conflicts)
        problems.extend(
            f"unsupported_execution.{item}"
            for item in self.execution_coverage_gaps()
        )
        for label, rv in ((
            ("salario", self.contract.salario),
            ("jornada", self.contract.jornada),
            ("admissao", self.contract.admissao),
            ("demissao", self.contract.demissao),
        )):
            if rv is None or rv.unresolved:
                problems.append(f"contract.{label}")
        required_process = (
            "numero", "justica", "regiao", "vara", "estado", "municipio",
            "data_liquidacao_atualizacao",
        )
        for key in required_process:
            if self.processo.get(key) in (None, ""):
                problems.append(f"processo.{key}")
        if not self.verbas:
            problems.append("verbas")
        for i, verba in enumerate(self.verbas):
            tipo = verba.tipo.lower()
            if not verba.descricao:
                problems.append(f"verba[{i}].descricao")
            if not verba.assunto_cnj and not verba.codigo_assuntos_cnj:
                problems.append(f"verba[{i}].assunto_cnj")
            if tipo in {"informada", "informed"}:
                if verba.valor is None or verba.valor.unresolved:
                    problems.append(f"verba[{i}].valor")
            elif tipo not in {"reflexo", "reflex"}:
                for name, rv in (("base", verba.base), ("divisor", verba.divisor), ("quantidade", verba.quantidade)):
                    if rv is None or rv.unresolved:
                        problems.append(f"verba[{i}].{name}")
            if tipo in {"calculada", "calculated"} and (
                verba.percentual is None or verba.percentual.unresolved
            ):
                problems.append(f"verba[{i}].percentual")
        return problems

    def execution_coverage_gaps(self) -> List[str]:
        """Campos que a UI oficial ainda não recebe neste executor.

        A especificação de domínio é deliberadamente mais rica que a tela
        automatizada. Sem esta guarda, um pagamento, reflexo ou incidência
        poderia ser persistido no JSON e simplesmente desaparecer na UI,
        produzindo um cálculo diferente sem qualquer erro visível.
        """
        gaps: List[str] = []
        collection_fields = (
            "salary_history", "reflexos", "fgts", "inss", "irpf", "costs",
            "fees", "fines", "payments", "judicial_adjustments", "vacations",
            "absences", "timecards", "update_parameters", "calculation_period",
        )
        for field_name in collection_fields:
            value = getattr(self, field_name)
            if value not in (None, [], {}):
                gaps.append(field_name)

        # These fields affect the official result but are not wired by
        # ``populate_verbas``.  They must be represented by an imported PJC or
        # by a future explicit selector mapping, never silently ignored.
        unsupported_verba_fields = (
            "codigo", "tipo_base", "percentual", "base", "valor_pago",
            "juros_sumula_439", "gera_reflexo", "gerar_principal",
            "compor_principal", "zera_valor_negativo", "exclusoes",
            "bases_compostas", "tipo_divisor", "tipo_quantidade",
            "proporcionalidade", "incidencias", "reflexos", "comentarios",
            "indices",
        )
        for index, verba in enumerate(self.verbas):
            for field_name in unsupported_verba_fields:
                value = getattr(verba, field_name)
                if value not in (None, False, "", [], {}):
                    gaps.append(f"verbas[{index}].{field_name}")
        return gaps

    def is_resolvable(self, mode: str = "STANDARD") -> bool:
        return not self.critical_parameters(mode=mode)


for _model in (FgtsSpec, FeeSpec, VacationSpec, AbsenceSpec, TimecardSpec, CalculationSpec):
    _model.model_rebuild()
