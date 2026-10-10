# GBrain v2 — predeploy controlado (SEM deploy automatico)

Status: **NO-GO** em 2026-10-10. O benchmark sintetico congelado passa, mas sete casos novos com referencias ficticias sao erroneamente FACT_VERIFIED. A etapa de deploy esta bloqueada ate correcoes comprovadas.

## Fontes e escopo

- Fonte isolada: `/tmp/gbrain-v2-recovery-20261010` (branch `integration/gbrain-v2-recovery-20261010`).
- Runtime instalado: `/usr/local/lib/haos-agent`.
- Modulos versionados a comparar e empacotar: `hermes/platform/context/memory/{candidate,classifier,router}.py`, `hermes/platform/memory/{dream,reconciler}.py`.
- Dependency-preflight deve validar todos os imports do runtime, inclusive dependencias nao presentes no diff.
- Preservar `/root/.haos/memory/*.db`, `/root/.haos/kanban/`, `vectors.db.corrupt`, untracked do Git e arquivos historicos.
- Nao modificar login/WebUI, firewall ou tunel Tailscale. Nao usar `sudo`.

## Preflight (somente leitura)

1. Confirmar origem e versao com `git status`, `git rev-parse HEAD` e hashes `sha256sum` dos arquivos do pacote e instalados; nao executar com cherry-pick pendente.
2. Executar a regressao existente (473 testes no baseline) e a avaliacao INEDITA `independent_validation/test_new_adversarial_cases.py`: requer 11/11 passados (hoje 7 falham).
3. Adicionar avaliacao holdout revisada por outro agente, invariantes de proveniencia, referencias de evidencia realmente resolvidas/verificadas; caminhos formatados como URI nao sao prova.
4. Verificar o gate antes e depois da serializacao, reconciliacao, staging, Dream e writer terminal; assegurar que alguem nao possa injetar `FACT_VERIFIED` com evidence_refs arbitrarias.
5. Executar testes com `PYTHONPATH` apontando para worktree e depois import smoke de runtime em ambiente isolado. Verificar WebUI/Tailscale HTTP 200 e status dos dois servicos.
6. Evidenciar plano de rollback e abrir janela de mudanca. Obter autorizacao explicita para implantar; nao executar este documento como script.

## Backup transacional antes de qualquer implante autorizado

1. Criar caminho exclusivo `/root/haos-deploy-backups/gbrain-v2-<timestamp>/` e manifesto com hash, owner, permissao, conteudo e versao de origem.
2. Copiar todos os modulos instalados afetados, distinguindo arquivo ausente (`classifier.py`) de arquivo existente. Registrar versao de units/drop-ins dos services.
3. Para **cada** arquivo SQLite online de producao, utilizar Python `sqlite3.Connection.backup()` para um destino separado; verificar `PRAGMA integrity_check` e quantidade de copias (nao copiar bancos vivos com `cp`). Sem compactar ou apagar originais.
4. Testar leitura das copias; nao armazenar segredos do banco no relatorio.

## Implementacao controlada apos GO e autorizacao

1. Fechar a branch de release e congelar SHA/testes; escolher rollout por canary se possivel, mantendo reversao imediata.
2. Copiar os arquivos versionados por estagio, verificacao de hash, permissao e troca atomica; NAO substituir diretorios inteiros nem arquivos nao rastreados.
3. `py_compile` e smoke tests antes do restart. Reiniciar SOMENTE os servicos necessarios; confirmar API e journal.
4. Reexecutar testes relevantes contra o runtime instalado e checar operacao do Dream, reconciliacao e router com fixtures sem DB real.
5. Se falhar: interromper rollout; restaurar exatamente os arquivos originais do backup e a ausencia de arquivos que nao existiam, corrigir permissoes e reiniciar; confirmar API/status/logs. Restaurar DB APENAS se migracao foi autorizada e comprovadamente necessaria; nao reverter automaticamente bancos ativos.
6. Registrar hashes, janela, resultado, IDs e ticket de mudanca; abrir novo GO/NO-GO pos-deploy.

## Motivo tecnico do bloqueio

O filtro `_filter_valid_evidence` valida sintaxe de URI, nao existencia, propriedade nem conteudo; `classify_assertion` transforma qualquer referencia aceita em `FACT_VERIFIED`. Gates downstream aceitam `FACT_VERIFIED` com referencias nao vazias sem comprovar o objeto. Corrigir end-to-end, nao apenas o regex.
