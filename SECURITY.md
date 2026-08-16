# Segurança

## Escopo

O repositório contém uma integração local com o PJe-Calc. O MCP fornecido
atualmente usa stdio e presume que o processo pai é confiável; ele não é um
servidor HTTP autenticado.

Antes de disponibilizar uma instância pública, implemente transporte HTTP,
autenticação, autorização por usuário e job, isolamento de arquivos, limites
de tamanho/tempo, rate limiting e auditoria de chamadas.

## Dados que não devem ser publicados

Não envie para o Git:

- credenciais, tokens ou arquivos `.env`;
- PDFs de processos, PJC, banco H2, logs e screenshots;
- diretórios `.jobs`, `.runtime`, `.tools` e `.venv`;
- binários cuja redistribuição não esteja autorizada.

Use `.env.example` apenas como modelo sem valores reais.

## Reporte

Não publique tokens ou detalhes exploráveis em issue pública. Para um
repositório público, use o canal privado de vulnerabilidades do GitHub quando
disponível ou contate o mantenedor antes de divulgar o problema.
