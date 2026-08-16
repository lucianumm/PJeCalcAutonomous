# Contribuindo

## Preparação

```bash
python -m venv .venv
python -m pip install -e ".[mcp,selenium,pdf,dev]"
```

## Antes de enviar alterações

```bash
python -m pytest -q
python -m compileall -q pjecalc_auto scripts tests
git diff --check
```

Não inclua processos reais, credenciais, artefatos de execução ou binários
não autorizados. Mudanças que afetam o fluxo oficial devem manter o princípio
fail-closed e acrescentar um teste de regressão.
