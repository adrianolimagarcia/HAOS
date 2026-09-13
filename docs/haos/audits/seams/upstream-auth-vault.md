# Auditoria — Hermes auth vault surface (seam real para K4)

Status: `entregue` · Frente 3 · leitura-only (nenhum arquivo alterado além deste relatório)

## Escopo

Mapear o subsistema de auth/token/vault do Hermes upstream (fork em
`HERMES-TURBO`) para que o scaffold HAOS possa substituir os mocks in-memory
`hermes/platform/auth/vault.py` + `secret_broker.py` por um wrapper real do
armazenamento upstream (`hermes_cli/auth*.py`), com E2E sob `HERMES_HOME`
temporário, grants por agente/ferramenta e redação de segredos. Nada foi
modificado no tree além deste arquivo; nenhum teste foi executado.

## Achados-chave

1. **O auth upstream NÃO é criptografado em repouso.** Não há keyring, AES,
   Fernet nem master key em nenhum `hermes_cli/auth*.py`. O "vault" é um JSON
   texto-plano `$HERMES_HOME/auth.json` com modo `0o600` + pai `0o700`
   (hermes_cli/auth.py:441-451, 669-719). `cryptography` só aparece no source
   Bitwarden (agent/secret_sources/bitwarden.py:242/254).
2. **Layout do store** (`hermes_cli/auth.py`): JSON `{version:1, providers:
   {provider_id: state}, credential_pool: {provider_id: [entry,...]},
   active_provider, suppressed_sources, updated_at}` (vazio: 617-618;
   verificado ao vivo sob HERMES_HOME=tmp → `auth.json` modo 0600). Lock
   cross-process via flock em `auth.json.lock` (`_auth_store_lock`, 602-614);
   escrita atômica `_save_auth_store` (705) com fallback `auth.json.corrupt`
   (639).
3. **Stores secundários**: `<root>/shared/nous_auth.json` (espelho do token
   Nous compartilhado entre profiles; auth_nous.py:268, 272-303) e singleton
   `.anthropic_oauth.json` na raiz do HERMES_HOME (auth_oauth_grants.py:29,
   465-466). Tokens Qwen ficam fora do Hermes em `~/.qwen/oauth_creds.json`
   (auth_qwen.py:25-26).
4. **Acesso a token utilizável é centralizado e refresh-aware**:
   `hermes_cli.auth.OAUTH_PROVIDER_FLOWS` (auth.py:1707-1719) mapeia
   `provider_id → {resolve_fn, status_fn}`; ex.: nous →
   `resolve_nous_runtime_credentials` (auth_nous.py:941), codex →
   `resolve_codex_runtime_credentials` (auth_codex.py:379), xai →
   `resolve_xai_oauth_runtime_credentials` (auth_xai.py:417). Tokens com
   `expires_at` futuro curto-circuitam antes de qualquer rede (auth.py:1607).
5. **Superfície de leitura/escrita que um wrapper chamaria**:
   - put: `write_credential_pool(provider_id, entries, *, removed_ids=None,
     status_cleared_ids=None)` (auth.py:926) e `_save_active_provider_state`
     (786); por provider: `persist_nous_credentials` (auth_nous.py:743),
     `_save_codex_tokens` (auth_codex.py:145), `_save_xai_oauth_tokens`
     (auth_xai.py:127), `_minimax_save_auth_state` (auth_minimax.py:161).
   - get: `read_credential_pool(provider_id=None)` (auth.py:853; sem arg =
     dict merge provider_id→entries, 863-871) e `get_provider_auth_state`
     (1017).
   - delete: `clear_provider_auth(provider_id=None)` (auth.py:1172) remove o
     provider inteiro; remoção de 1 entry = `write_credential_pool(provider,
     [], removed_ids=[id])` (939-962).
   - list keys: `read_credential_pool()` sem arg (863-871) ou CLI
     `auth_list_command` (hermes_cli/auth_commands.py:382).
6. **Imports bare OK** (python3.14.7, cwd do repo, sem venv, nada instalado):
   `hermes_cli.auth`, `auth_constants`, `auth_nous`, `auth_commands`,
   `auth_codex`, `auth_oauth_grants`, `auth_device_flow`, `auth_xai`,
   `auth_spotify`, `hermes.platform.auth.vault` e
   `hermes.platform.auth.secret_broker` — todos importaram e imprimiram
   `__file__` sem erro. httpx é lazy (auth_constants.py:21-45).
7. **Redação de segredos tem helper upstream reutilizável**: `agent/redact.py`
   — `redact_sensitive_text(text, *, force=False, code_file=False,
   file_read=False, redact_url_credentials=False)` (558), `mask_secret` (405),
   `redact_terminal_output` (701), hook de plugin `register_redaction_patterns`
   (835). Ligado por default via `security.redact_secrets`
   (hermes_cli/config.py:2261-2270) → `HERMES_REDACT_SECRETS` (redact.py:28).
   Já aplicado a texto de prompt/contexto: agent/chat_completion_helpers.py:
   224-226 e 1605-1606; agent/context_compressor.py:1006; agent/context_engine
   .py:26 (`force=True`). Os módulos `hermes_cli/auth*.py` não têm redactor.
8. **Resolução do HERMES_HOME**: ContextVar override
   (`set_hermes_home_override`, hermes_constants.py:25-31) → env `HERMES_HOME`
   (87) → default `~/.hermes`/`LOCALAPPDATA/hermes` (45-51);
   `get_default_hermes_root` (146). Testes usam HERMES_HOME=tmp_path
   (tests/conftest.py sandbox). Cintos de segurança pytest: auth.py:441-451
   (recusa store real) e auth_nous.py:291-302 (exige HERMES_SHARED_AUTH_DIR
   tmp para o store Nous compartilhado).

## Lacunas acionáveis

1. **Wrapper real do store não existe no scaffold.**
   - O quê: `SecretBroker`/`OAuthVault` de `hermes/platform/auth/` são só
     dicts in-memory; nada persiste, nada faz refresh, nada redige.
   - Onde upstream: `hermes_cli/auth.py` (926/853/1172) e `OAUTH_PROVIDER_FLOWS`
     (auth.py:1707).
   - Por que importa: K4 pede "Auth/SecretBroker real" com persistência
     cifrada sob HERMES_HOME; hoje qualquer secret some no restart e nenhum
     fluxo OAuth real funciona.
2. **Grants por agente/ferramenta não têm análogo upstream.**
   - O quê: o upstream só modela credenciais por *provider* (credential_pool),
     nunca grants por agente/ferramenta; o HAOS projeta `store_secret(
     "openai:primary", …)`.
   - Onde upstream: `read_credential_pool`/`write_credential_pool` keyed por
     provider_id (auth.py:853/926); entry carrega `id`/`auth_type`.
   - Por que importa: a semântica de "grant" precisa ser decidida — mapear o
     sufixo da ref (`provider:label`) para `credential_pool[provider]` com
     `entry.id=label`, ou criar política própria no HAOS (não existe no
     upstream).
3. **"Cifrado em repouso" não é o modelo upstream.**
   - O quê: K4 fala em "encrypted persistence"; o upstream confia em 0600/0700.
   - Onde upstream: auth.py:441-451, 669-719 (sem cipher).
   - Por que importa: ou o K4 aceita a semântica upstream (0600 + home 0700,
     padrão do Hermes), ou o HAOS precisa adicionar cifra própria — não há
     infra upstream para reutilizar.
4. **Redação para prompts não está no caminho do HAOS.**
   - O quê: o scaffold não chama nenhum redactor; a meta K4 exige "secret
     redaction".
   - Onde upstream: `agent/redact.py:558` (`redact_sensitive_text(force=True)`)
     — o helper canônico já usado em texto de contexto/prompt
     (agent/context_engine.py:26).
   - Por que importa: sem reuso, o HAOS criaria redactor próprio divergente e
     vazaria segredos em logs/contexto do modelo.
5. **Testabilidade sem OAuth real ainda não está demonstrada no scaffold.**
   - O quê: K4 pede E2E com HERMES_HOME temporário; os cintos de segurança
     pytest exigem HERMES_HOME e HERMES_SHARED_AUTH_DIR em tmp.
   - Onde upstream: auth.py:441-451; auth_nous.py:291-302; padrão de teste
     `profile_env` (tests/hermes_cli/test_profiles.py) citado no AGENTS.md.
   - Por que importa: um wrapper que não respeitar esses cintos corrompe o
     auth real do usuário em CI; tokens com `expires_at` futuro evitam rede.

## Status de implementação

- Mocks in-memory existem como scaffold não-trackeado:
  `hermes/platform/auth/vault.py` (28 linhas: `SecretBroker` 3-13, `OAuthVault`
  15-28) e `hermes/platform/auth/secret_broker.py` (re-export, 1-3). Consumidor
  único: `hermes/platform/haos_server.py:28` (só importa; não instancia).
- Teste único cobrindo o mock (in-memory, unittest):
  `tests/platform/test_full_platform.py:27-35` (`test_auth_secret_broker`).
- **Nenhum achado acima virou código/teste real ainda**: não existe import de
  `hermes_cli.auth` em `hermes/platform/` (verificado por grep); nenhum teste
  E2E sob HERMES_HOME temporário; nenhum uso de `agent/redact` no scaffold.
- Nota git: `hermes/`, `haos.py`, `tests/platform/`, `docs/haos/` são
  não-trackeados; o `hermes` do HEAD é um blob marcado deletado (` D hermes`).

## Recomendações

1. **SecretBroker = adapter fino** sobre `hermes_cli.auth`, com import lazy
   dentro das funções (mantém `hermes/platform` stdlib-only no nível de módulo,
   espelhando a regra facade↔sibling): `store_secret` → `write_credential_pool`
   (auth.py:926); `resolve_credential` → fatia de `read_credential_pool` (853)
   pelo `entry.id`; delete → `removed_ids` (939); list → `read_credential_pool()`
   sem arg (863-871). Segredos não-provider: sem casa upstream — usar um
   provider reservado do pool ou `.env`, nunca um segundo store.
2. **OAuthVault.get_valid_token** → despacho por
   `hermes_cli.auth.OAUTH_PROVIDER_FLOWS[provider].resolve()` (auth.py:
   1707-1719; ex. auth_nous.py:941); `register_profile` →
   `_save_active_provider_state` (786) com `expires_at` ISO upstream.
3. **E2E sem OAuth real**: HERMES_HOME + HERMES_SHARED_AUTH_DIR em tmp_path,
   semear tokens pelas funções reais (`write_credential_pool`/
   `_save_active_provider_state`), `expires_at` no futuro (curto-circuito em
   auth.py:1607) e monkeypatch dos `_refresh_*`/`refresh_*_pure` para o caminho
   de refresh. Testes em `tests/platform/` sob o sandbox do tests/conftest.py.
4. **Redação**: reusar `agent/redact.redact_sensitive_text(..., force=True)`
   (redact.py:558); E2E = gravar via store real, montar string tipo-prompt com o
   segredo, assertar que a cópia redigida não contém o token e que `auth.json`
   em disco ainda o guarda.
5. **Decisão explícita de cifra**: registrar na spec K4 se aceita 0600/0700
   upstream ou se o HAOS cifra por conta própria (sem helper upstream).
