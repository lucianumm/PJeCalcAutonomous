"""ProcessCorpus → CalculationSpec (extração documental, fail-closed).

Corrige o ERRO A: `calculate_from_process` não encerrava mais em `CORPUS_BUILT`.
Esta camada converte o corpus extraído pelo AuditorProcessual em uma
`CalculationSpec` auditável, sem nunca inventar valores.

Categorias documentais reconhecidas (chaves de `Fact.category`):
    admissao, demissao, periodo_calculo, ajuizamento, salario, jornada,
    verba, reflexo, base, percentual, divisor, quantidade, fgts, inss, irpf,
    custas, honorarios, multa, pagamento, deducao, indice, juros.

Cada fato vira `ResolvedValue` com status DOCUMENTED e proveniência (peça +
página + trecho). O que não foi encontrado permanece UNRESOLVED e, em strict
mode, bloqueia a liquidação.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from .calculation_spec import (
    CalculationSpec,
    ContractSpec,
    Provenance,
    ResolvedValue,
    SourceStatus,
    VerbaSpec,
    parse_br_decimal,
    parse_date,
)
from .corpus import Fact, ProcessCorpus


def _rv(fact: Fact) -> ResolvedValue:
    try:
        status = SourceStatus(fact.status)
    except ValueError:
        status = SourceStatus.DOCUMENTED
    category = fact.category.lower()
    value = fact.value
    normalization_note = fact.note
    document_date = None
    if fact.document_date:
        try:
            document_date = parse_date(fact.document_date)
        except ValueError:
            document_date = None
    try:
        if category in {
            "salario", "base", "percentual", "divisor", "multiplicador",
            "quantidade", "valor", "pagamento", "multa", "honorarios",
        }:
            value = parse_br_decimal(value)
        elif category in {
            "admissao", "demissao", "ajuizamento", "data_ultima_atualizacao",
            "data_liquidacao", "data_inicio_calculo", "data_termino_calculo",
            "periodo_inicial", "periodo_final",
        }:
            value = parse_date(value)
    except ValueError as exc:
        value = None
        status = SourceStatus.CONFLICTING
        normalization_note = f"valor documental inválido: {exc}"
    return ResolvedValue(
        value=value,
        status=status,
        provenance=[
            Provenance(
                source=fact.piece or "corpus",
                status=status,
                document_id=fact.document_id,
                file_sha256=fact.file_sha256,
                page=fact.pdf_page,
                page_pdf=fact.pdf_page,
                page_processual=fact.court_page,
                excerpt=fact.excerpt,
                confidence=fact.confidence,
                authority=fact.authority,
                document_date=document_date,
            )
        ],
        note=normalization_note,
    )


def _unresolved() -> ResolvedValue:
    return ResolvedValue(value=None, status=SourceStatus.UNRESOLVED,
                         note="parâmetro crítico ausente na fonte documental")


def build_spec(corpus: ProcessCorpus,
               case_id: Optional[str] = None,
               strict_mode: bool = True) -> CalculationSpec:
    spec = CalculationSpec(case_id=case_id or corpus.process_id,
                           strict_mode=strict_mode)
    # ``manifest.source`` describes the input file, not process fields.  Keep
    # it in provenance/input_hashes and populate the process only from facts
    # that were explicitly extracted below.
    spec.processo = {}

    contract = ContractSpec()
    contract.admissao = _unresolved()
    contract.demissao = _unresolved()
    contract.salario = _unresolved()
    contract.jornada = _unresolved()

    verbas: List[VerbaSpec] = []

    handled_categories = {
        "admissao", "demissao", "salario", "jornada", "ajuizamento",
        "processo_numero", "numero_processo", "digito", "ano", "justica",
        "regiao", "vara", "estado", "municipio", "reclamante", "reclamado",
        "data_ultima_atualizacao", "data_liquidacao", "data_inicio_calculo",
        "data_termino_calculo", "verba", "base", "divisor", "multiplicador",
        "quantidade", "percentual", "descricao",
    }
    for fact_index, f in enumerate(corpus.facts):
        c = f.category.lower()
        rv = _rv(f)
        if c == "admissao":
            contract.admissao = _merge_resolved(spec, "contract.admissao", contract.admissao, rv)
        elif c == "demissao":
            contract.demissao = _merge_resolved(spec, "contract.demissao", contract.demissao, rv)
        elif c == "salario":
            contract.salario = _merge_resolved(spec, "contract.salario", contract.salario, rv)
        elif c == "jornada":
            contract.jornada = _merge_resolved(spec, "contract.jornada", contract.jornada, rv)
        elif c == "ajuizamento":
            _merge_process(spec, "data_ajuizamento", str(rv.value), rv)
        elif c in {"processo_numero", "numero_processo"}:
            _merge_process(spec, "numero", str(rv.value), rv)
        elif c in {"digito", "ano", "justica", "regiao", "vara", "estado", "municipio"}:
            _merge_process(spec, c, str(rv.value), rv)
        elif c == "reclamante":
            _merge_process(spec, "reclamante", str(rv.value), rv)
        elif c == "reclamado":
            _merge_process(spec, "reclamado", str(rv.value), rv)
        elif c in {"verba", "reflexo", "base", "percentual", "divisor",
                   "quantidade", "multa", "honorarios"}:
            # atributos são preenchidos no segundo passe (verba*-chave)
            pass
        if c not in handled_categories:
            spec.unsupported_facts.append(f"{fact_index}:{c}")

    # segundo passe: associa verbas por sufixo (verba_N, divisor_N, ...)
    specs = _group_verbas(corpus.facts)
    spec.verbas = specs

    # data da última liquidação / atualização
    for f in corpus.facts:
        if f.category.lower() in {
            "data_ultima_atualizacao", "data_liquidacao",
            "data_inicio_calculo", "data_termino_calculo",
        }:
            rv = _rv(f)
            target = "data_liquidacao_atualizacao"
            if f.category.lower() == "data_inicio_calculo":
                target = "data_inicio_calculo"
            elif f.category.lower() == "data_termino_calculo":
                target = "data_termino_calculo"
            _merge_process(spec, target, str(rv.value), rv)

    spec.contract = contract
    return spec


def _merge_resolved(spec: CalculationSpec, key: str,
                    current: Optional[ResolvedValue], incoming: ResolvedValue) -> ResolvedValue:
    if current is None or current.value is None:
        return incoming
    if incoming.value is None or current.value == incoming.value:
        current.provenance.extend(incoming.provenance)
        return current
    spec.conflicts.append(key)
    return ResolvedValue(
        value=None,
        status=SourceStatus.CONFLICTING,
        provenance=[*current.provenance, *incoming.provenance],
        note="fontes documentais apresentam valores divergentes",
    )


def _merge_process(spec: CalculationSpec, key: str, value: object,
                   incoming: ResolvedValue) -> None:
    spec.process_provenance.setdefault(key, []).extend(incoming.provenance)
    if incoming.value is None:
        spec.processo[key] = None
        if key not in spec.conflicts:
            spec.conflicts.append(f"processo.{key}")
        return
    existing = spec.processo.get(key)
    if existing in (None, "None"):
        spec.processo[key] = value
    elif existing != value:
        spec.processo[key] = None
        spec.conflicts.append(f"processo.{key}")


def _group_verbas(facts: Iterable[Fact]) -> List[VerbaSpec]:
    by_idx: dict = {}
    for f in facts:
        c = f.category.lower()
        idx = 0
        if "_" in c:
            base, _, n = c.rpartition("_")
            if n.isdigit():
                idx = int(n)
                c = base
        v = by_idx.setdefault(idx, {})
        if c == "verba":
            _merge_verba_field(v, "tipo", str(f.value))
        if c in ("base", "divisor", "multiplicador", "quantidade", "percentual"):
            _merge_verba_field(v, c, _rv(f))
        if c == "descricao":
            _merge_verba_field(v, "descricao", str(f.value))

    out = []
    for idx in sorted(by_idx):
        data = by_idx[idx]
        v = VerbaSpec(tipo=data.get("tipo", "Principal"),
                      descricao=data.get("descricao"))
        v.base = data.get("base")
        v.divisor = data.get("divisor")
        v.multiplicador = data.get("multiplicador")
        v.quantidade = data.get("quantidade")
        v.percentual = data.get("percentual")
        if data.get("_conflict"):
            for field_name in data["_conflict"]:
                if field_name in {"base", "divisor", "multiplicador", "quantidade", "percentual"}:
                    setattr(v, field_name, ResolvedValue(
                        value=None, status=SourceStatus.CONFLICTING,
                        note="fatos duplicados divergentes",
                    ))
                elif field_name == "descricao":
                    v.descricao = None
                elif field_name == "tipo":
                    v.tipo = "CONFLICTING"
        out.append(v)
    return out


def _merge_verba_field(target: dict, key: str, value: object) -> None:
    if key not in target:
        target[key] = value
        return
    current = target[key]
    equal = current.value == value.value if isinstance(current, ResolvedValue) and isinstance(value, ResolvedValue) else current == value
    if not equal:
        target.setdefault("_conflict", []).append(key)


# ---------------------------------------------------------------------------
# Extração determinística a partir do corpus do AuditorProcessual.
# ---------------------------------------------------------------------------
import json
import re
from pathlib import Path as _Path

# Padrões documentais (somente extração explícita; sem inventar valores).
_PATTERNS = [
    ("admissao", re.compile(
        r"(?:admitid[oa]|admiss[ãa]o)\s+(?:em\s+)?(\d{1,2}/\d{1,2}/\d{2,4})",
        re.IGNORECASE)),
    ("demissao", re.compile(
        r"(?:demitid[oa]|demiss[ãa]o|dispensad[oa])\s+(?:em\s+)?(\d{1,2}/\d{1,2}/\d{2,4})",
        re.IGNORECASE)),
    ("salario", re.compile(
        r"(?:sal[áa]rio|remunera[çc][ãa]o)\s+(?:de\s+)?R?\$\s*([\d.,]+)",
        re.IGNORECASE)),
    ("jornada", re.compile(
        r"jornada\s+(?:de\s+)?(\d+(?:[.,]\d+)?)\s*(?:horas|h\b)", re.IGNORECASE)),
    ("percentual", re.compile(
        r"(?:percentual|percentuais|al[íi]quota)\s+(?:de\s+)?(\d+(?:[.,]\d+)?)\s*\%",
        re.IGNORECASE)),
]


def load_facts_from_corpus(corpus_dir: _Path) -> ProcessCorpus:
    """Lê o corpus do AuditorProcessual e produz um ProcessCorpus com Facts.

    Extrai apenas valores explícitos via padrões documentais. Tudo que não for
    encontrado fica fora do corpus (e vira UNRESOLVED na spec).
    """
    manifest_path = corpus_dir / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    facts: List[Fact] = []

    # Saída estruturada do AuditorProcessual é a fonte primária. Regex só é
    # fallback quando nenhum fato estruturado está disponível.
    for candidate in (corpus_dir / "facts.json", corpus_dir / "facts.jsonl"):
        if not candidate.is_file():
            continue
        if candidate.suffix == ".json":
            raw_facts = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(raw_facts, dict):
                raw_facts = raw_facts.get("facts", [])
        else:
            raw_facts = [json.loads(line) for line in candidate.read_text(encoding="utf-8").splitlines() if line.strip()]
        if isinstance(raw_facts, list):
            facts.extend(Fact.model_validate(item) for item in raw_facts if isinstance(item, dict))
        break

    if not facts and isinstance(manifest.get("facts"), list):
        facts.extend(Fact.model_validate(item) for item in manifest["facts"] if isinstance(item, dict))

    # The AuditorProcessual manifest carries a process identifier even when
    # no semantic fact extractor was available.  Preserve that documented
    # value as a fact (with the source-file hash) rather than copying the whole
    # ``source`` metadata object into CalculationSpec.processo.
    source = manifest.get("source") if isinstance(manifest, dict) else None
    source_hash = source.get("sha256") if isinstance(source, dict) else None
    process_id = manifest.get("process_id") if isinstance(manifest, dict) else None
    if process_id and not any(f.category.lower() in {
        "processo_numero", "numero_processo"
    } for f in facts):
        facts.append(Fact(
            category="processo_numero",
            value=process_id,
            piece="manifest.json",
            document_id=str(process_id),
            file_sha256=source_hash,
            excerpt=f"process_id={process_id}",
            authority="AuditorProcessual manifest",
            status=SourceStatus.DOCUMENTED,
        ))

    # texto estruturado (se existir)
    md_path = None
    for cand in ("processo_estruturado.md", "processo_completo.md"):
        p = corpus_dir / cand
        if p.exists():
            md_path = p
            break

    if md_path is not None and not facts:
        text = md_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            # página: tenta capturar marcadores "PDF p. N" / "fl. M"
            page = None
            pm = re.search(r"PDF\s+p\.\s*(\d+)", line, re.IGNORECASE)
            if pm:
                page = int(pm.group(1))
            for category, pattern in _PATTERNS:
                m = pattern.search(line)
                if m:
                    facts.append(Fact(
                        category=category,
                        value=m.group(1),
                        piece=md_path.name,
                        pdf_page=page,
                        excerpt=line.strip()[:300],
                        status="EXTRACTED",
                    ))

    # processo/partes a partir do manifest source
    src = manifest.get("source") or {}
    if isinstance(src, dict):
        name = src.get("name")
        if name:
            # Tenta separar reclamante/reclamado por " x " / "X" only when
            # the source name actually contains party names.  A generic PDF
            # filename must not become a fabricated party fact.
            sp = re.split(r"\s+[xXc/C]\s+", name, maxsplit=1)
            if len(sp) == 2 and all(len(part.strip()) > 2 for part in sp):
                facts.append(Fact(
                    category="reclamante", value=sp[0].strip(),
                    piece="manifest.json", file_sha256=source_hash,
                    status=SourceStatus.DOCUMENTED,
                ))
                facts.append(Fact(
                    category="reclamado", value=sp[1].strip(),
                    piece="manifest.json", file_sha256=source_hash,
                    status=SourceStatus.DOCUMENTED,
                ))

    return ProcessCorpus(
        process_id=manifest.get("process_id", str(corpus_dir.name)),
        manifest=manifest,
        facts=facts,
    )


def build_spec_from_corpus(corpus_dir: _Path,
                           strict_mode: bool = True) -> CalculationSpec:
    """Constrói a CalculationSpec a partir do diretório de corpus."""
    corpus = load_facts_from_corpus(corpus_dir)
    return build_spec(corpus, strict_mode=strict_mode)
