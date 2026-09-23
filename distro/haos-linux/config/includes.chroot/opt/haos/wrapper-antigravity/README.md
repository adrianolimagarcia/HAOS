# oauth-antigravity-linux

**oauth-antigravity-linux** é um proxy leve e eficiente desenvolvido em Node.js que traduz requisições compatíveis com o protocolo do OpenAI Chat Completions (`/v1/chat/completions`) para a API interna do Google Antigravity (Cloud Code Assist / Gemini). 

Ele permite que frameworks de IA e ferramentas locais (como o **DeepSeek Harness - DSH**) utilizem os modelos **Gemini 3.7 Flash** e **Gemini 3.7 Pro** da sua licença oficial via fluxo de autenticação OAuth (obtido do CLI `agy`).

---

## 🛠️ Como Funciona (Arquitetura)

O proxy atua como um middleware local intermediário:

```
[Cliente / DSH] ──(OpenAI API: /v1/chat/completions)──> [antigravity-proxy (Port 8790)]
                                                              │
                                                   (Tradução de Protocolo)
                                                              │
                                                              ▼
[Google API] <──(Antigravity: streamGenerateContent)──────────┘
```

---

## ⚙️ Explicação de Cada Parte do Código (`server.mjs`)

### 1. Configurações e Aliases de Modelo (`Config`)
* **Porta do Servidor:** Configurada por `ANTIGRAVITY_PROXY_PORT` (padrão `8787`, mas rodando localmente na `8790`).
* **Tokens de Acesso:** O código busca o token de atualização (refresh token) gerado pela autenticação oficial do Antigravity CLI (`~/.gemini/antigravity-cli/antigravity-oauth-token`).
* **Endpoints:** Lista de URLs internas do Google Assist. O proxy tenta primeiro o endpoint que respondeu 200 por último (por padrão o `daily-cloudcode-pa` — canais diários têm os modelos mais novos), com fallback para produção e sandboxes.
* **MODEL_ALIASES:** De-para entre os nomes pedidos pelos clientes (DSH) e os ids canônicos que o upstream Antigravity realmente aceita. **Cada geração (Gemini 3 Flash, 3.5, 3.6, 3.7, 3.8) é um modelo distinto no backend** — o proxy NÃO colapsa gerações num único id. Catálogo validado empiricamente (2026-09-05) via `v1internal:fetchAvailableModels` + chamadas `generateContent` reais: nomes com sufixo de geração (ex.: `gemini-3.7-flash-high`) não existem no upstream (404) e são mapeados para o id canônico da MESMA geração (`gemini-3.7-flash-tiered`), com o nível de reasoning carregado no `thinkingConfig`.

### 2. Autenticação (`OAuth token management`)
* **`getAccessToken()` e `refreshAccessToken()`**: Gerenciam a expiração do token de acesso do Google. Se o token atual expirar em menos de 120 segundos, o proxy faz uma requisição HTTP às APIs de OAuth da Google usando as credenciais do cliente do Antigravity, renovando a sessão sem interrupções.

### 3. Gerenciamento de Projetos (`Project context`)
* **`ensureProject()`**: O Antigravity exige um projeto Google Cloud associado a cada requisição (geralmente `aicode-consumers`). O proxy descobre e valida este projeto realizando chamadas rápidas para `loadCodeAssist` e `onboardUser`, fazendo cache do resultado para evitar sobrecarga.

### 4. Tradução de Ida (`translateOpenAiToGemini`)
Converte o payload JSON enviado pelo DSH (mensagens, ferramentas, parâmetros) para o formato esperado pelo Gemini:
* **Estrutura de Mensagens:** Traduz as `role` de *user*, *system*, *tool* e *assistant*.
* **Reasoning Passback:** Passa o raciocínio anterior da IA (`reasoning_content`) de turnos anteriores como um bloco de pensamento estruturado (`{ text: msg.reasoning_content, thought: true }`). Isso permite que o Gemini mantenha a consistência lógica.
* **Mapeamento de Ferramentas (Tool Calling):** As saídas e entradas de ferramentas em formato de texto cru (como retornos de shell) são envelopadas em objetos JSON para satisfazer as restrições rígidas de `google.protobuf.Struct` do backend do Google.
* **Thinking Config Dinâmico:** Determina o nível de raciocínio desejado. Se o modelo selecionado for do tipo `gemini-2.5`, ativa `thinkingBudget: 2048`. Se for da família `gemini-3`, ativa `thinkingLevel: low/medium/high` com base no sufixo do modelo.

### 5. Tradução de Volta (`translateGeminiToOpenAi` / `streamOpenAiResponse`)
Faz o mapeamento inverso das respostas obtidas do Google para o DSH:
* **Fatiamento de Texto SSE:** A API do Gemini envia fragmentos puros (deltas não-cumulativos). O proxy repassa esses fragmentos instantaneamente em formato de chunks da OpenAI.
* **Raciocínio no Streaming:** Fragmentos identificados como pensamentos (`p.thought`) são enviados sob a chave `reasoning_content` no payload do stream, permitindo que o DSH mostre a caixa de pensamento ("Thinking...") em tempo real na interface gráfica.
* **Tratamento de Cancelamento do Cliente:** Registra um manipulador de desconexão. Se você parar de gerar na interface gráfica do DSH, o proxy envia um comando de cancelamento ativo ao leitor do stream do Google (`reader.cancel()`), poupando banda e limite de cota de API.

---


* **Contrato 429 retryable:** rate limit é devolvido ao cliente como `429` com `type: "rate_limit_error"` (e `Retry-After` quando o upstream envia). Antes, o 429 virava `502 upstream_error` — erro fatal que o DSH não conseguia retryar. Agora o `retryPolicy` do provider no DSH (que inclui `RATE_LIMIT`) faz backoff e retenta sozinho.
* **429 é por-endpoint, não da conta (verificado empiricamente):** com a mesma conta, `cloudcode-pa.googleapis.com` pode responder `Resource has been exhausted` e `autopush` `Individual quota reached` enquanto `daily-cloudcode-pa.googleapis.com` responde **200 OK** — mesmo com o painel mostrando 93% de cota semanal livre. Cada endpoint tem quota individual própria. Por isso o proxy **nunca aborta** o loop de endpoints em 429: sempre tenta todos.
* **Suporte a múltiplas contas para fallback automático (Novo v1.3.0):** o proxy cria um pool dinâmico carregando todas as contas autorizadas na máquina (a conta padrão do `agy` CLI e as contas habilitadas do `opencode-accounts.json`). Se a conta ativa estourar o limite (429) em todos os endpoints de fallback, o proxy **chaveia para a próxima conta de forma 100% transparente** e executa a requisição no mesmo momento. A última conta bem-sucedida é lembrada para chamadas futuras.
* **4 endpoints de fallback** (produção, daily, autopush-sandbox, daily-sandbox). O `daily-cloudcode-pa.googleapis.com` (sem `.sandbox`) foi descoberto extraindo as strings do binário oficial do CLI `agy` — é o endpoint do canal daily de produção e está confirmado funcional. Ordem atual: `cloudcode-pa` → `daily-cloudcode-pa` → `autopush.sandbox` → `daily-cloudcode-pa.sandbox`.
* **Atualização dinâmica sem restart:** o proxy monitora os arquivos de credenciais. Se você adicionar uma conta nova ao arquivo `/root/.config/opencode/antigravity-accounts.json`, ela entra no pool em até 1 minuto sem precisar reiniciar o serviço.
* **Backoff exponencial com jitter** entre tentativas, com teto configurável (`ANTIGRAVITY_MAX_RATE_BACKOFF_MS`, padrão 30s) e respeito ao `Retry-After` do upstream.
* **Timeouts em todas as chamadas upstream** (`ANTIGRAVITY_TIMEOUT_MS`, padrão 120s): em streams SSE, o timeout de headers é desarmado (`cancelOnHeaders: true`) assim que os primeiros headers HTTP 200 chegam, garantindo que transmissões longas de raciocínio ou código nunca sejam abortadas por um timer fixo no corpo.
* **Idle timeout no stream** (`ANTIGRAVITY_STREAM_IDLE_MS`, padrão 120s): governa exclusivamente a inatividade real entre chunks recebidos do upstream.
* **Semáforo de concorrência de ciclo completo** (`ANTIGRAVITY_MAX_CONCURRENT`, padrão 30): a vaga no semáforo é retida durante toda a transmissão do stream SSE até o seu término/aborto real (e não liberada prematuramente na chegada dos headers).
* **Propagação de Erros Reais de Transporte:** se a conexão com o upstream falhar (socket reset, timeout ou EOF inesperado), o proxy emite um chunk SSE estruturado (`UPSTREAM_FAILURE`) para que o DSH/harness acione seu mecanismo de retry nativo, em vez de mascarar a falha como uma resposta vazia de sucesso (`STOP/[DONE]`).
* **Preservação de `id` em Function Calling (Gemini 3.8):** tanto `functionCall` quanto `functionResponse` preservam o `call_id` original emitido pelo harness, garantindo correlação determinística em chamadas paralelas de ferramentas.
* **Cache de `thoughtSignature` real:** o proxy mantém um cache em memória das assinaturas criptográficas reais emitidas pelo Gemini 3 em chamadas de ferramentas, devolvendo-as fielmente nos turnos subsequentes.
* **Conformidade com o Gemini 3.8 Migration Guide:** remoção automática de `temperature` e `top_p` (incompatíveis com o modo de raciocínio do 3.8) e clamp estrito de `maxOutputTokens` em 65.536.
* **Session ID Estável para Afinidade de Prefix Cache:** o `sessionId` é derivado deterministicamente do contexto inicial em vez de usar UUID aleatório por request, permitindo que a Google aproveite 100% de hit em Prompt Prefix Caching.
* **Singleton no refresh do token:** requests concorrentes compartilham a MESMA promise de refresh (antes, N requests com token expirado disparavam N refreshes paralelos — race condition).
* **Backpressure no stream:** quando o buffer do socket enche (cliente lento), o proxy aguarda `drain` antes de continuar — memória não cresce sem limite.
* **Sanitização de `function_response.name`:** nome de tool result nunca é enviado vazio ao Gemini (fallback `tool_result_N`), evitando o 400 `Name cannot be empty` observado em histórico compactado.
* **`max_completion_tokens` aceito** (novo nome OpenAI), além do clássico `max_tokens`.
* **Credencial do cliente via env:** `ANTIGRAVITY_CLIENT_SECRET` (e `ANTIGRAVITY_CLIENT_ID`) substituem o valor embutido quando definidas.
* **Observabilidade:** cada request gera uma linha `REQ <model> [stream] -> <status> in <ms> [usage=...]` no log — consumo de cota, latência e erros agora são mensuráveis.
* **Thought-to-Content Fallback (Prevenção de Aborto Silencioso):** No Gemini 3.8 / 3.7 Flash em modo thinking, o modelo ocasionalmente disserta todo o plano no canal de pensamento (`thought: true`), mas não emite texto nem chamadas de ferramenta no mesmo turno, concluindo com `STOP`. O proxy detecta quando há raciocínio acumulado mas nenhum texto/tool_call e promove o raciocínio para o canal `content`, evitando que o DeepSeek Harness encerre o turno no vazio.
* **Garantia de `finish_reason: "tool_calls"`:** Quando o Gemini fecha em `STOP` mas gerou chamadas de função, o proxy mapeia incondicionalmente para `finish_reason: "tool_calls"`.
* **Sanitização de Assinaturas de Pensamento (Gemini 3):** Pensamentos passados sem assinatura criptográfica são convertidos para texto comum de contexto, impedindo que o validador interno de `thoughtSignature` da Google aborte a execução no meio do raciocínio.
* **Testes de contrato e streaming:** `test/contrato.test.mjs` (43 casos) cobre tradução ida/volta, struct wrapping, passback de reasoning, deltas de tool calls, contratos 429 e streaming SSE de thinking com tool calls. Rode com `node test/contrato.test.mjs`.

### Variáveis de ambiente (todas opcionais)

| Variável | Padrão | Descrição |
|---|---|---|
| `ANTIGRAVITY_PROXY_PORT` | `8787` | Porta do proxy |
| `ANTIGRAVITY_TOKEN_FILE` | `~/.gemini/antigravity-cli/antigravity-oauth-token` | Arquivo do refresh token |
| `ANTIGRAVITY_CLIENT_ID` / `ANTIGRAVITY_CLIENT_SECRET` | valor embutido | Credenciais OAuth do cliente |
| `ANTIGRAVITY_PROJECT` | `aicode-consumers` | Projeto managed |
| `ANTIGRAVITY_THINKING_LEVEL` | `low` | Nível de raciocínio padrão |
| `ANTIGRAVITY_MAX_CONCURRENT` | `30` | Requests upstream simultâneos |
| `ANTIGRAVITY_TIMEOUT_MS` | `120000` | Timeout por chamada upstream (2 min) |
| `ANTIGRAVITY_STREAM_IDLE_MS` | `120000` | Idle timeout do stream (2 min) |
| `ANTIGRAVITY_MAX_RATE_BACKOFF_MS` | `30000` | Teto do backoff em 429 |
| `ANTIGRAVITY_DEBUG` | `0` | `1` registra no log o corpo Gemini sanitizado de cada request |
| `ANTIGRAVITY_DEBUG_MAX` | `200000` | Tamanho máximo (bytes) do corpo logado em DEBUG |
| `ANTIGRAVITY_RECORD_TOOLS` | — | Caminho de arquivo: grava o array `tools` do **primeiro** request (schemas reais do DSH) para o teste de snapshot |

---

## 🚀 Como Executar

### Pré-requisitos
Certifique-se de que a máquina VPS possui a autenticação do `agy` feita:
```bash
agy auth login # Caso precise reautenticar
```

### Executando Manualmente
```bash
node server.mjs
```

### Rodando via Systemd (Serviço de Segundo Plano)
O proxy está configurado para rodar permanentemente sob o systemd.

* **Iniciar o proxy:** `systemctl start antigravity-proxy.service`
* **Ver os logs de execução:** `journalctl -u antigravity-proxy.service -f`
* **Reiniciar o proxy:** `systemctl restart antigravity-proxy.service`

### Rodando no Windows

O proxy é **Node.js puro** (sem dependências nativas) e roda nativamente no Windows. Passos:

1. **Instale o Node.js** (LTS) em https://nodejs.org.
2. **Instale o agy CLI** (Google Antigravity): no PowerShell, execute `irm https://antigravity.google/cli/install.ps1 | iex` (ver [docs oficiais](https://antigravity.google/docs/cli/install/)) e faça login com `agy auth login`.
3. **Copie o repositório** para uma pasta local (ex.: `C:\antigravity-proxy\`).
4. **Execute o proxy:**
   ```powershell
   cd C:\antigravity-proxy
   node server.mjs
   ```
5. **(Opcional) Como serviço do Windows** — use o [NSSM](https://nssm.cc/) para rodar em segundo plano:
   ```powershell
   nssm install AntigravityProxy "C:\Program Files\nodejs\node.exe" "C:\antigravity-proxy\server.mjs"
   nssm start AntigravityProxy
   ```

**Diferenças automáticas no Windows:**
* O arquivo de contas do opencode é procurado em `%APPDATA%\opencode\antigravity-accounts.json` (no Linux/macOS é `~/.config/opencode/`). Se suas contas estiverem em outro lugar, defina `ANTIGRAVITY_OPENCODE_ACCOUNTS`.
* O refresh token do agy é procurado em `%USERPROFILE%\.gemini\antigravity-cli\antigravity-oauth-token` (o agy grava lá nas três plataformas). Se necessário, defina `ANTIGRAVITY_TOKEN_FILE`.
* Os logs vão para `proxy.log` ao lado do `server.mjs` (mesmo comportamento).

---

## 📝 Licença
Este repositório é de uso privado e exclusivo.
