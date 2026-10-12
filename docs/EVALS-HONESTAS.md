# Evals honestas: Ablation Ledger + teste de Goodhart

> Protocolo mínimo para testar novas heurísticas, prompts e skills do HAOS
> sem auto-engano. Inspirado nos *conceitos* da pesquisa lean-agent
> (reimplementação limpa — nenhum código copiado).

## O problema

É fácil "provar" que uma mudança ajuda:

- medir só onde ela funciona (cherry-picking de tarefas);
- aceitar a primeira métrica que sobe (métrica boa demais para ser verdade);
- recompensar menos linhas e receber código ilegível (lei de Goodhart:
  quando a medida vira meta, ela para de medir o que media).

Este guia define dois rituais baratos que bloqueiam esses três vícios.

---

## 1. Ablation Ledger (o livro-razão da ablação)

**Ideia central.** Um sistema é um pacote, e pacotes escondem caronas:
peças que custam tokens e turnos e não fazem nada. O ledger remove **uma
peça por vez** e roda o resultado nas tarefas onde aquela peça deveria
importar, comparando com o sistema completo nas mesmas tarefas.

### Regras

1. **Uma métrica-alvo pré-declarada por peça.** Antes de rodar, escreva
   qual métrica a peça deveria mover (ex: "o detector X deveria reduzir
   falsos positivos no eval Y"). Sem métrica pré-declarada, o resultado
   não conta.
2. **Remova uma peça por vez.** Full system vs full-minus-piece, mesmas
   tarefas, mesmas réplicas.
3. **Veredito por regra fixa:**
   - **KEPT** — remover a peça piora a métrica de forma mensurável.
   - **HARMFUL** — remover a peça *melhora* a métrica. Remova a peça
     do sistema (ela estava atrapalhando).
   - **NEUTRAL** — sem diferença mensurável. Remova a peça (custo sem
     benefício é débito técnico).
4. **Exceção:** proteções da lista "never cut" (validação de input,
   segurança, prevenção de perda de dados, acessibilidade, pedido
   explícito do usuário) não passam pelo ledger — elas protegem uma
   promessa, não uma métrica.

### Template de registro

```markdown
## Ablation: <nome da peça>
- Data: YYYY-MM-DD
- Peça removida: <o que foi desligado>
- Métrica-alvo (pré-declarada): <métrica + direção esperada>
- Tarefas: <quais evals, quantas réplicas>
- Resultado full: <número>
- Resultado sem a peça: <número>
- Veredito: KEPT | HARMFUL | NEUTRAL
- Ação: <manter / remover / investigar>
```

Guarde os registros em `evals/ablation-ledger/` (um `.md` por peça).
O ledger é o que justifica a configuração final: o sistema que roda em
produção é o sistema completo **menos** tudo que o ledger não manteve —
e o registro mostra o porquê.

---

## 2. Teste de Goodhart (o detector de "golf")

**Ideia central.** Qualquer sistema que recompensa menos linhas convida o
agente a escrever *menos código legível*: statements unidos por `;`,
lambdas aninhados, comprehensions de três níveis, variáveis de uma letra.
Isso não é código proporcional — é a mesma complexidade comprimida em
linhas piores.

### Regra

Todo eval que mede "menos código" (ou qualquer proxy de simplicidade)
DEVE rodar junto um **braço de controle** instruído a "preferir
one-liners" e um check de legibilidade:

1. Rode a mudança candidata nas tarefas.
2. Rode o braço "one-liner" nas mesmas tarefas.
3. Compare: se a candidata se parece com o braço one-liner (linhas
   curtas + legibilidade baixa), ela está *golfando*, não simplificando.
4. **Legibilidade mínima é gate:** use `scripts/detect_overengineering.py`
   (shape `prose-padding` não; o inverso — densidade) + revisão humana.
   Heurísticas baratas: linhas > 120 chars, mais de 3 níveis de
   aninhamento em uma expressão, nomes de 1 letra fora de loops.

### Sinais de golf (não de simplicidade)

- `;` unindo statements;
- lambda atribuído a nome (`f = lambda x: ...`);
- comprehension aninhada com condicional duplo;
- abreviações que só o autor entende.

---

## 3. Ledger de resultados negativos

**Ideia central.** Um relatório que conta os sucessos no texto e esconde
as falhas no rodapé mente pelo layout. Todo experimento deste protocolo
gera uma linha na tabela de negativos — **gerada a partir dos registros**,
para não deixar de fora o resultado que preferiríamos esquecer.

### Template

```markdown
## Resultados negativos — <experimento>
| Data | Hipótese | O que aconteceu | Veredito |
|------|----------|-----------------|----------|
| YYYY-MM-DD | <o que esperávamos> | <o que medimos> | refutada / sem efeito / piorou |
```

Peças removidas pelo ablation ledger entram aqui automaticamente
(veredito NEUTRAL/HARMFUL). Probes que a mudança piorou entram aqui.
Modelos onde o efeito não transferiu entram aqui.

---

## Quando aplicar

- **Sempre** que uma nova skill/heuristic de "simplificação" ou
  "performance de prompt" for proposta.
- **Sempre** que um prompt for encurtado "para economizar tokens".
- **Opcional** para mudanças funcionais puras (sem claim de
  simplicidade/performance).

## Checklist rápido (para o autor do eval)

- [ ] Métrica-alvo pré-declarada antes de rodar?
- [ ] Braço de controle rodado nas mesmas tarefas?
- [ ] Ablation por peça (se o sistema tem >1 peça nova)?
- [ ] Teste de Goodhart (se a claim envolve "menos"/"mais simples")?
- [ ] Negativos registrados na tabela?
- [ ] `scripts/trim_guard.py` passou (nada protegido foi cortado)?
