# Contrato do escritor Rust (v2)

**Status:** especificação bloqueada — os testes de aceitação deste documento falham contra o
`haos-edge` atual. Esta fase congela o contrato; não implementa o escritor nem altera `state.db`.

**Contrato base:** `haos-edge.readonly-sse.v1` (commit `15373e618c`). O v1 continua válido para
as superfícies de leitura/SSE. O v2 adiciona somente a fronteira interna de escrita tipada.

**Identificador:** `haos-edge.rust-writer.v2`

## 1. Limites e invariantes

- O escritor Rust é uma fronteira HTTP interna do `haos-edge`, acessível apenas por loopback
  (`127.0.0.1`/`::1`) ou pelo transporte IPC local que o edge já expõe. Não há endpoint público.
- Cada instância do edge inicia com `--profile <id>` e `--data-dir <absolute-path>` explícitos.
  Ambos são binding imutável da instância. Ausência de qualquer um encerra o startup com falha;
  não existe fallback para `HAOS_HOME`, `HERMES_HOME`, `HAOS_DATA_DIR`, `~/.haos` ou `/tmp`.
- Cada request repete `profile` e `data_dir`. O servidor compara os valores com o binding de
  startup antes de abrir qualquer banco. Divergência retorna `profile_mismatch` ou
  `data_dir_mismatch`.
- A autorização é um token interno configurado pelo supervisor do edge. Cookie de WebUI, senha de operador e ausência de token não autenticam esta rota. Comparação do token deve ser em tempo constante; o token não aparece em logs, respostas ou erros.
- `state.db` tem um único escritor lógico. A operação completa é transacional; timeout ou erro
  não pode deixar metade da operação persistida.
- A request não contém SQL, nome de tabela, coluna arbitrária, expressão SQL ou texto a executar.
  O servidor seleciona uma operação de um registro fechado e usa SQL parametrizado interno.
- O schema SQLite não é migrado por este contrato. `schema_version` é versão do payload/resultado
  do contrato, não uma instrução para alterar o schema do banco.

## 2. Endpoint e envelopes

**Endpoint:** `POST /internal/rust-writer/v2/operations`

Headers obrigatórios:

```text
Content-Type: application/json
Authorization: Bearer <internal-token>
```

Request mínima:

```json
{
  "contract_version": "haos-edge.rust-writer.v2",
  "schema_version": 2,
  "profile": "default",
  "data_dir": "/var/lib/haos",
  "operation": "set_session_title",
  "idempotency_key": "title:session-123:1",
  "timeout_ms": 2000,
  "payload": {
    "session_id": "session-123",
    "title": "Título novo"
  }
}
```

Regras do envelope:

| Campo | Regra |
|---|---|
| `contract_version` | obrigatório e exatamente `haos-edge.rust-writer.v2`; versão desconhecida falha fechado |
| `schema_version` | obrigatório, inteiro `2`; versões não suportadas não são convertidas silenciosamente |
| `profile` | obrigatório, identificador explícito; deve ser igual ao binding da instância |
| `data_dir` | obrigatório, caminho absoluto explícito; deve ser igual ao binding normalizado da instância |
| `operation` | obrigatório e pertencente ao registro fechado da seção 3 |
| `idempotency_key` | obrigatório; ASCII `[A-Za-z0-9][A-Za-z0-9._:-]{0,199}`; escopo é `(profile, key)` |
| `timeout_ms` | obrigatório, inteiro entre 1 e 30000; é deadline total do servidor, não apenas timeout de socket |
| `payload` | obrigatório; objeto específico da operação; campos desconhecidos ou tipos errados falham |

Resposta de sucesso:

```json
{
  "contract_version": "haos-edge.rust-writer.v2",
  "schema_version": 2,
  "profile": "default",
  "data_dir": "/var/lib/haos",
  "operation": "set_session_title",
  "idempotency_key": "title:session-123:1",
  "ok": true,
  "result": {"session_id": "session-123", "changed": true}
}
```

Resposta de erro (sem SQL e sem segredo):

```json
{
  "contract_version": "haos-edge.rust-writer.v2",
  "schema_version": 2,
  "profile": "default",
  "data_dir": "/var/lib/haos",
  "operation": "set_session_title",
  "idempotency_key": "title:session-123:1",
  "ok": false,
  "error": {
    "code": "idempotency_conflict",
    "message": "idempotency key was already used with another request",
    "retryable": false
  }
}
```

`message` é diagnóstico estável e não pode incluir token, senha, SQL, caminho de outro perfil ou
conteúdo de mensagem. Clientes decidem por `error.code`, nunca por texto livre.

## 3. Registro fechado de operações tipadas

Somente estas operações fazem parte da v2 inicial. Não existe operação genérica `execute_sql`,
`query`, `statement`, `mutation` ou equivalente.

| Operação | Payload obrigatório e opcional |
|---|---|
| `create_session` | `session_id`, `source`; opcional `title`, `model`, `cwd`, `parent_session_id`, `profile_name` |
| `append_messages` | `session_id`, `messages` (lista não vazia de objetos tipados `role`, `content`; `api_content`, `timestamp`, `message_id` opcionais) |
| `update_session` | `session_id`; alterações tipadas e limitadas a `model`, `provider`, `cwd`, `git_branch`, `git_repo_root`, `profile_name` |
| `set_session_archived` | `session_id`, `archived` (booleano) |
| `set_session_hidden` | `session_id`, `hidden` (booleano) |
| `set_session_pinned` | `session_id`, `pinned` (booleano) |
| `set_session_read` | `session_id`, `read` (booleano) |
| `set_session_title` | `session_id`, `title` (string) |
| `update_session_cwd` | `session_id`, `cwd`; opcional `git_branch`, `git_repo_root` |
| `update_session_meta` | `session_id`, `model_config` (objeto JSON); opcional `model` |
| `set_message_reaction` | `session_id`, `message_row_id` (inteiro positivo), `emoji` (string ou `null`) |
| `delete_session` | `session_id`, opcional `delete_transcript` (booleano, default `false`) |
| `archive_and_compact` | `session_id`, `messages` (lista tipada não vazia), `summary` (string), `reason` (string) |

Os nomes acima são a API; a implementação pode compartilhar transações e funções internas, mas
não pode expor SQL ao cliente nem aceitar campos que alterem a seleção da query. Cada operação
retorna um `result` tipado, ou um erro do registro da seção 5.

## 4. Idempotência e timeout

- A primeira request aceita grava o resultado associado a `(profile, idempotency_key)` na mesma
  transação lógica da operação.
- Repetição com a mesma chave e fingerprint idêntico retorna o mesmo status e resultado, sem
  repetir efeitos. Isso vale após retry, reconexão e restart do processo.
- Mesma chave com fingerprint diferente retorna HTTP `409` e `idempotency_conflict`; nunca
  sobrescreve o resultado original.
- O fingerprint inclui `contract_version`, `schema_version`, `operation`, `profile`, `data_dir`
  e `payload` canonicalizado. O token de autenticação não é incluído.
- O servidor inicia um deadline monotônico ao aceitar a request. Se o deadline expirar antes do
  commit, faz rollback e retorna HTTP `408`/`timeout`. O cliente pode repetir com a mesma chave.
  O servidor não retorna `ok: true` depois de timeout.
- Uma operação aceita não pode ser executada duas vezes por retries concorrentes com a mesma chave.
  Uma delas aguarda o resultado ou recebe `busy` conforme a política de lock; nunca há dual-write.

## 5. Códigos de erro

| HTTP | `error.code` | Retryável | Condição |
|---:|---|:---:|---|
| 400 | `invalid_request` | não | JSON, tipo, campo ou limite inválido |
| 400 | `unsupported_contract_version` | não | `contract_version` não suportado |
| 400 | `unsupported_schema_version` | não | `schema_version` não suportado |
| 400 | `unknown_operation` | não | operação fora do registro |
| 400 | `missing_idempotency_key` | não | chave ausente |
| 400 | `invalid_idempotency_key` | não | formato ou tamanho inválido |
| 400 | `invalid_timeout` | não | `timeout_ms` fora de 1–30000 |
| 401 | `unauthenticated` | não | Authorization ausente/malformado/token inválido |
| 403 | `forbidden` | não | caller não é uma origem interna permitida |
| 409 | `profile_mismatch` | não | profile da request difere do binding |
| 409 | `data_dir_mismatch` | não | data_dir da request difere do binding |
| 409 | `idempotency_conflict` | não | chave reutilizada com fingerprint diferente |
| 404 | `not_found` | não | sessão ou mensagem alvo inexiste |
| 408 | `timeout` | sim | deadline expirou antes do commit |
| 409 | `conflict` | não | transição/estado incompatível |
| 423 | `busy` | sim | lock do escritor ou da sessão não disponível no deadline |
| 500 | `internal` | não | erro interno não classificável; sem detalhes sensíveis |
| 503 | `storage_unavailable` | sim | banco ausente, indisponível ou não pode ser aberto |
| 503 | `schema_mismatch` | não | banco não satisfaz o schema congelado |
| 503 | `read_only` | não | instância não é o escritor autorizado |

## 6. Relação com o v1 e gates de aceitação

O v1 não ganha writes por compatibilidade implícita. Leitura/SSE v1 continua read-only; a rota v2
é nova, versionada e autenticada internamente. Nenhum cliente pode enviar uma operação v2 para
rota v1, e nenhum endpoint v1 pode aceitar `operation`, `payload` ou SQL para produzir escrita.

Antes de qualquer flag `state_writer: rust`, os testes de `tests/haos_edge/test_rust_writer_contract_v2.py`
devem ficar verdes, incluindo:

1. envelope/versionamento, registro fechado e validação de tipos;
2. auth interna rejeitando ausência/token inválido antes de abrir o banco;
3. binding explícito e isolamento A→B→A com `profile` e `data_dir` reais;
4. idempotência em retry, concorrência e fingerprint conflitante;
5. timeout com rollback observável e códigos estáveis;
6. ausência de SQL no protocolo e rejeição de operação genérica;
7. readback da operação bem-sucedida e preservação do schema/fixtures.

No estado atual, os casos de integração são **BLOCKED** intencionalmente: o crate ainda não
implementa a rota v2, usa resolução/fallback de `HAOS_DATA_DIR` no startup, possui handlers de
sessão sem auth interna v2 e contém superfícies Rust que escrevem SQLite fora deste contrato.
Nenhuma dessas lacunas é corrigida nesta fase.
