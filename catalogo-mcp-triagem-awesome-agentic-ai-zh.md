# Triagem do catálogo MCP/Skills — awesome-agentic-ai-zh

**Data:** 2026-10-05 · **Fonte:** `WenyuChiou/awesome-agentic-ai-zh` (MIT), `resources/mcp-skills-catalog.en.md` (56.509 bytes, 1.146 linhas) — cópia durável em `fontes/mcp-skills-catalog.en.md` (neste diretório).
**Status:** triagem documental (não é adoção nem teste de segurança). Detalhamento ficha-a-ficha dos 49 condicionais não foi persistido pelo agente de triagem; consultar o catálogo-fonte ao lado.

## Contagens
- 81 fichas de projetos em 17 categorias (excluídos 2 cabeçalhos editoriais e o convite a contribuir).
- Triagem preliminar: **49 candidatos condicionais** · **17 redundantes** com o HAOS · **15 arquivados/fora da lacuna genérica**.

## Sobreposições com o HAOS (não absorver)
Capacidades já cobertas nativamente: browser/CDP, `web_search`, `web_extract`, acesso a arquivos, delegação (`delegate_task`, plugins/MCPs de delegação). Servidores nessas categorias marcados como redundantes.

## Candidatos condicionais (categorizados no catálogo-fonte)
Integrações SaaS, bancos de dados, observabilidade, design, pesquisa — cada um só vale se houver lacuna real + necessidade + revisão de permissões antes de ativar.

## Diretrizes de autorização (critério geral de triagem, do próprio catálogo)
1. Menor privilégio: determinar leitura vs. escrita antes de ativar.
2. Começar read-only e com dados de teste.
3. Aprovação humana obrigatória antes de write/send/delete.
4. Preferir pontos de integração oficiais.
5. Campos editoriais do catálogo: `License` e `Rating` em todas as 81 fichas; `Delivery` tabular em 6. OAuth, read/write e maturidade NÃO são dimensões tabulares uniformes — verificar ficha a ficha.

## Estado do HAOS na época da triagem
- `config.yaml`: MCPs `ouroboros` e `haos-edge` habilitados.
- Plugins: `adspirer`, `agent-fix-lab`, `dsh-bridge`, `hermes-project-stewardship`, `memory-recall`, `paperclip`.
- Diretórios em `/root/.haos/mcp`: `perplexity-direct`, `common`, `chatgpt-direct`, `haos-direct`, `shell`, `cookies` (existência de diretório não prova atividade).
