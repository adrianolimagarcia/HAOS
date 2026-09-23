# Fase E — integração reversível do `haos-prov`

## Seleção do motor

`tools/adr_prov.py` continua sendo o ponto de entrada compatível. O oráculo
Python foi preservado em `tools/adr_prov_legacy.py`; o shim só faz `exec` do
Rust quando `HAOS_PROV_BIN` está explicitamente definido para um executável
existente (ou nome resolvível no `PATH`). Sem essa variável, com binário
inexistente, ou se ela apontar para o próprio shim, a execução cai
 deterministicamente no Python legado. O `exec` preserva argv, streams e
exit code do Rust.

```bash
HAOS_PROV_BIN=/caminho/para/haos-prov python tools/adr_prov.py --json check
```

`haos-edge prov ...` é uma fronteira fina no binário existente: tenta
`HAOS_PROV_BIN` e, se não estiver utilizável, executa `HAOS_PROV_PY` (ou
`tools/adr_prov.py`) via `PYTHON`/`python3`. Não há motor PROV duplicado.

## Rollback

1. Remova `HAOS_PROV_BIN` do ambiente (`unset HAOS_PROV_BIN`).
2. Reinicie o caller; a rota Python é o padrão.
3. Para retirar somente o fast-path do edge, reverta o commit da integração;
   `tools/adr_prov.py` e `tools/adr_prov_legacy.py` podem permanecer.

Nenhum arquivo em `/root/.haos` é alterado nesta fase. O evaluator externo
`/root/.haos/evals/eval_proveniencia.py` fica pendente para Fase F. A paridade
independente permanece em `scripts/prov_parity.py`.
