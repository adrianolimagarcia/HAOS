---
id: ADR-007
titulo: "Extração de PDF: pymupdf4llm como caminho local, docling descartado"
status: "Aceito"
data: 2026-09-13
decidido_por: "human:adriano"
autor: "adriano"
contexto: "/usr/local/lib/haos-agent/venv · skill productivity/pdf · tools/read_extract.py"
causado_by:
  - "container docling-serve redundante e pesado com benchmark demonstrando superioridade de pymupdf4llm"
  - "renumerado de ADR-005 em 2026-09-15 conforme ADR-006"
evidence:
  - "benchmark em 4 PDFs onde docling foi ~1.5x mais lento"
  - "overhead desnecessário de container GPU vs biblioteca local python"
affects:
  - "tools/read_extract.py"
  - "skills/productivity/pdf"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:benchmark-extracao-pdf"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "audit:benchmark-docling-vs-pymupdf"
  causado_by: "desempenho inferior e peso operacional do docling-serve"
  affects:
    - "tools/read_extract.py"
    - "skills/productivity/pdf"
  supersedes: []
  superseded_by: null
---

# ADR-007 — Extração de PDF: pymupdf4llm como caminho local, docling descartado

**Status:** Aceito (dono decidiu em 2026-09-13: "pare o docling exclua o container vamos usar o pymupdf4llm")
**Data:** 2026-09-13
**Contexto técnico:** `/usr/local/lib/haos-agent/venv` · skill `productivity/pdf` · `tools/read_extract.py` (caminho do `read_file`)
**ADR relacionada:** —
**Renumerada:** era `ADR-005-extracao-de-pdf-pymupdf4llm` até 2026-09-15, quando o dono a renumerou para ADR-007 por colisão de número com `ADR-005-caminho-canonico-do-vault-e-escopo-dos-grafos` (diagnóstico em ADR-006). Só o número mudou — a decisão e o texto originais estão intactos.

## Contexto

O HAOS extrai PDF pelo `read_file`, que delega a `tools/read_extract.py` → **`firecrawl-anydoc`** (dependência core). Existe também a skill `productivity/pdf`, que documenta um caminho leve (`pymupdf`/`pymupdf4llm`) e um caminho de qualidade (`marker-pdf`, ~3–5 GB).

Nenhum desses caminhos opcionais estava **instalado** no host — eram documentação sem binário. A skill ainda declarava `pymupdf (~25MB)`, número que não correspondia à realidade.

Paralelamente, o nó mantinha um container `docling-serve` (`docling-serve-cu126:main`) desde 2026-09-10, com passthrough de GPU.

A pergunta que disparou esta decisão veio da avaliação do marketplace do Dify: candidatos de documento (MinerU, md_exporter, markitdown, PaddleOCR) — todos redundantes com o que o HAOS já tem (skills nativas `pdf`/`docx`/`xlsx`/`powerpoint`, `tesseract`, `ffmpeg`).

## Medição (o que decidiu)

Comparativo docling-serve 1.12.0 × pymupdf4llm em 4 PDFs com camada de texto, <1 MB: BERT (760 K), ResNet (804 K), seq2seq (436 K), Form W-9 IRS (140 K).

| Dimensão | Resultado |
|---|---|
| Velocidade | docling ~1,5× mais lento em 3 de 4 (12,18/9,85/7,66/3,95 s vs 7,95/7,09/5,50/4,99 s) |
| Tabelas de dados | **pymupdf4llm** preserva os decimais do original (`35.0`, `81.0`); docling normaliza para `35`, `81` — conferido contra `page.get_text("words")` |
| Formulário (W-9) | **docling** decisivo: texto linear + checkboxes `- [ ]`. O pymupdf4llm forçou o formulário numa tabela de 2 colunas, partiu frases no meio da célula e colou palavras (`Beforeyou`, `Forguidance`) |
| Cabeçalho/rodapé | empate (ambos limpos) |
| Ordem de leitura 2 colunas | empate (ambos corretos) |
| LaTeX / fórmulas | **nenhum dos dois** |
| VRAM | docling pinava **3.604 MiB de 4.096 MiB**; pymupdf4llm usa **zero** |

**Não há vencedor único.** A escolha foi feita pelo custo operacional e pelo caso dominante.

## Decisão

1. **`pymupdf4llm` é o extrator local padrão** para PDF no HAOS. Instalado no venv canônico: `pymupdf4llm` 1.28.2 + `pymupdf` 1.28.2 + `pymupdf-layout` 1.28.2 + `networkx` 3.6.1 — **124 MB adicionados** (onnxruntime e numpy já existiam).
2. **`docling-serve` desativado**: container parado e removido. Não substitui nada que o pymupdf4llm faça melhor no hardware atual, e o custo de VRAM (3,6 GB de 4 GB) inviabiliza coexistência com qualquer outra carga de GPU.
3. **`marker-pdf` permanece como opção pesada** para PDF escaneado ou denso em fórmula, não instalada.
4. **Não instalar nada do marketplace do Dify** para documento: redundante.

## Consequências

- **Ganho**: +3,6 GB de VRAM devolvidos; caminho de PDF sem GPU; tamanho de instalação 124 MB em vez de 15,6 GB de imagem + modelos.
- **Perda aceita**: documentos de formulário/layout não-tabular degradam no pymupdf4llm. Se isso virar recorrente, a mitigação é renderizar a página + `vision_analyze` (o `tesseract` está instalado), não reinstalar o docling.
- **Limitação aberta**: nenhum extrator local do nó produz LaTeX. PDF científico com fórmula continua sendo texto achatado.
- **O `read_file` continua usando `firecrawl-anydoc`** — o pymupdf4llm melhora o caminho dos scripts da skill, não o extrator padrão. Trocar o caminho padrão é decisão separada, não coberta por esta ADR.
- **Resíduos do docling**: imagem `docling-serve-cu126:main` (**15,6 GB**) ainda no disco; regra UFW `5001/tcp` e bind `0.0.0.0:5001` sem dono; `compose.yaml` preservado em `sdb/hermes/docling/`.

## Critérios de aceite

- [x] `pymupdf4llm` instalado no venv canônico e exercitado em PDF real (tabela GLUE presente, decimais preservados)
- [x] container `docling-serve` parado e removido; VRAM verificada em 410 MiB usados (de 4.014)
- [x] tamanho real medido e skill `productivity/pdf` corrigida (o número anterior, `~25MB`, era falso)
- [x] REGISTRY atualizado no mesmo turno (entrada de serviço marcada REMOVIDO + decisão datada)
- [ ] imagem de 15,6 GB removida e regra UFW 5001 fechada — aguarda decisão do dono
- [ ] caso de eval guardando o caminho de extração — pendente

## Evidência

- Comparativo e RCA completos: `okf/integracoes/docling-serve-x-pymupdf4llm-comparativo-medido-em-4-pdfs-rca-de-2-defeitos-do-container.md`
- Seleção de modelo/VRAM e o volume morto: `okf/integracoes/docling-serve-sele-o-de-modelo-vram-de-4-gb-e-o-volume-de-cache-que-nunca-usado.md`
- Extração de PDF no HAOS (anydoc vs pymupdf4llm, tamanhos): `okf/integracoes/extra-o-de-pdf-no-haos-anydoc-vs-pymupdf4llm-tamanhos-medidos-e-o-que-j-existe.md`
- Avaliação do marketplace Dify: `okf/integracoes/plugins-do-dify-n-o-s-o-absorv-veis-pelo-haos-extrair-biblioteca-nunca-plugin.md`
- REGISTRY: entrada de 2026-09-13 (docling removido) + serviço marcado REMOVIDO
