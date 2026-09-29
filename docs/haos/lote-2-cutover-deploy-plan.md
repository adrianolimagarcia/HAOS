# Plano de cutover e deploy — Lote 2 Rust/edge

**Status:** plano proposto; não é autorização para ativar Rust como writer nem para alterar produção.
**Regra atual:** Python continua sendo o único writer de `state.db` até aprovação explícita do cutover.
**Escopo:** implantação controlada do observer SSE e, em fase posterior e separada, do writer v2. Não inclui mudar schema nem fazer dual-write no mesmo DB.

## 1. Invariantes e pressupostos

- Não implantar um artefato que não tenha passado os gates read-only e writer aplicáveis.
- Toda flag deve ser explícita, persistente/configurável, default `python` para autoridade de escrita.
- Writer Rust desligado não pode alterar estado; observer não pode iniciar writer, checkpoint, lock de escrita ou criar/mutar DB/WAL/SHM.
- Autenticação/profile/data-dir são verificados antes de qualquer acesso SQLite.
- Sem fallback silencioso de `rust` para Python: falha explícita, alarme e rollback deliberado.
- Sem dual-write concorrente no mesmo `state.db`.
- Nenhuma migração de schema neste lote.
- O plano não define nomes de units, caminhos ou comandos de deploy ainda: validar no host-alvo e no workflow de release antes de executar.

## 2. Estados do rollout

1. **S0 — Python:** comportamento atual; Python único writer. Baseline observável.
2. **S1 — Rust observer em staging:** Rust serve somente leitura/SSE com DB de fixture/cópia; nenhuma autoridade de escrita.
3. **S2 — Rust observer canário em produção:** ativação limitada a um processo/caminho de leitura, Python segue writer; tráfego de escrita continua no Python.
4. **S3 — Writer shadow isolado:** Rust replica operações em DB shadow separado; Python segue writer de produção. Nunca compartilhar arquivo/WAL.
5. **S4 — Canary writer por domínio:** só após gates completos, backup verificado e aprovação explícita; um domínio por vez, flag reversível.
6. **S5 — Rust writer ampliado:** somente após janela de canário sem abortes e aceite explícito. Não é consequência automática do S4.

## 3. Gates antes de staging (S1)

### Observer/read-only

- 6/6 gates `test_readonly_sse_contract.py` passam contra servidor Rust real.
- Auth negativa demonstrada antes de abrir SQLite, com instrumentação comportamental (não teste de código-fonte).
- Identidade explícita e estável de profile/data-dir; A→B→A real sem vazamento.
- Resposta de sessões comparada com consumidor Python ativo e fixtures; diferenças justificadas, não campos inventados.
- Hashes byte-a-byte de DB, WAL e SHM antes/depois do processo observer, incluindo enquanto há replay SSE e quando os arquivos estão ausentes.
- Superfície do observer default-deny: endpoints mutáveis inacessíveis; ingest preservada no modo normal.
- `cargo test`, `cargo fmt --check`, `cargo clippy -D warnings` para escopo aplicável e `scripts/run_tests.sh` dos testes relacionados; compressão 52/52.
- Clippy preexistente não pode ser silenciosamente ignorado: baseline e deltas documentados; nenhum lint novo no diff. Para o gate de release, a política do repo deve ser satisfeita ou uma exceção aprovada.

### Writer v2 (bloqueia S4/S5; não bloqueia observer S1/S2)

- 8/8 gates contra rota/IPC real: auth independente, operações tipadas, sem SQL arbitrário, fingerprint idempotente, conflito estável, transação/rollback em falha e timeout, envelope de erro, lock por profile e constraints.
- Timeout testado por injeção controlada com readback provando ausência de escrita parcial.
- Perfil e data-dir mismatches rejeitados antes da abertura do banco.
- Python permanece autoritativo enquanto Rust é validado.

## 4. Shadow e canário

### S3 — Shadow isolado

- Clonar schema/dados para `state.shadow.db` por método aprovado e verificável; nunca apontar shadow ao arquivo vivo.
- Reproduzir operações deterministicamente em fixture e depois em janela controlada.
- Comparar estado lógico por tabelas/linhas relevantes, constraints, mensagens e compressão; preservar artefatos redigidos de evidência.
- Critério: zero divergências sem classificação e resolução; duração/janela e volume definidos antes do teste. Não presumir que 48h seja suficiente nem alegar equivalência com amostra pequena.
- Abort: divergência, erro de lock, crescimento inesperado, atraso, crash ou evidência de escrita no DB principal.

### S4 — Canary writer por domínio

- Exigir revisão humana do relatório de S3 e autorização explícita imediatamente antes da mudança.
- Backup consistente de DB + WAL/SHM usando mecanismo SQLite seguro; registrar hash/schema fingerprint e testar leitura/restauração da cópia em diretório isolado.
- Verificar ownership e exclusividade do lock; parar/retirar o writer Python daquele domínio antes de entregar autoridade Rust. Não pode haver sobreposição de writers.
- Mudar um domínio por vez com flag explícita; registrar valor efetivo e processo/versão ativos.
- Canary inicial restrito a staging; produção só depois do ensaio de rollback em staging.
- Observar erros por operação, lock contention, busy/timeout/rollback, latência p50/p95/p99, falhas de integridade/constraint, sessões/mensagens criadas, fila/eventos e crescimento dos arquivos. Definir limites absolutos com baseline real antes da janela; este plano não inventa thresholds.

## 5. Go / no-go / abort

**Go** somente quando todos os gates da fase, revisão de segurança, backup restaurável, telemetria, operador de rollback e aprovação humana estiverem confirmados.

**No-go/abort imediato:**
- qualquer alteração não autorizada ou byte diferente no DB durante observer;
- auth não comprovada antes do DB, perfil/data-dir ambíguo ou A→B→A com vazamento;
- divergência funcional, erro de constraint, operação perdida/duplicada ou idempotência inconsistente;
- lock ausente/duplo, escritor concorrente, rollback parcial;
- degradação além dos limites pré-registrados, crash loop ou perda de telemetria;
- backup/restauração não verificados ou rollback não ensaiado.

Ao abortar: congelar novos writes Rust, preservar logs/telemetria sem segredos, determinar a autoridade efetiva e impedir que dois writers concorram. Não restaurar backup sobre DB ativo sem procedimento de consistência e aprovação operacional.

## 6. Rollback

1. Desabilitar o domínio Rust pelo mecanismo de configuração suportado e confirmar readback da configuração efetiva.
2. Confirmar que o writer Rust parou e liberou lock antes de reativar o Python para o mesmo DB.
3. Se houve apenas erro sem corrupção: voltar Python e validar operações/readback. Não restaurar backup por reflexo.
4. Se houver suspeita de corrupção/perda: parar todos os writers, preservar DB/WAL/SHM e evidência, comparar schema/integridade; restaurar backup verificado somente com janela aprovada e após confirmar ponto de recuperação.
5. Executar probes de leitura/escrita Python, integridade SQLite e A→B→A; confirmar SSE e health.
6. Manter Rust desativado até RCA, correção, testes repetidos e nova aprovação.

O rollback deve ser ensaiado em staging e medido (tempo, perda potencial, operação manual requerida) antes de qualquer S4. O plano não considera suficiente um simples flip de flag se o estado já divergiu.

## 7. Evidência e registro

Cada fase anexa ao relatório do Lote 2: commit/artefato e hash, configuração efetiva (sem segredos), comandos e exit codes observados, testes/fixtures, hashes do DB/WAL/SHM, schema fingerprint, métricas comparadas, janela/volume, divergências, backup/restore receipt e resultado do rollback. Nunca registrar credenciais.

## 8. Pendências antes de execução

- Concluir e aceitar os 6 gates read-only e 8 gates writer em base atual.
- Resolver os lints Rust existentes ou obter exceção de release com delta explícito.
- Confirmar no host-alvo as units/processos, estratégia de publicação/rollback e caminhos canônicos.
- Definir métricas e limites de abort a partir de baseline medido.
- Revisão de compatibilidade da resposta sessions pelo consumidor real.
- Aprovação explícita do operador para cada transição S2→S3 e S3→S4.

**Estado final deste documento:** planejamento apenas; nenhum serviço, flag, writer, tráfego ou DB foi alterado por este plano.
