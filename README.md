<p align="center">
  <h1 align="center">HAOS ☤ (Hermes Agentic Operating System)</h1>
  <p align="center"><b>Multi-Agent Autonomous OS • Kanban Backbone • Self-Evolution Ouroboros • Ultrawork Mode • 100% Standalone</b></p>
</p>

<p align="center">
  <a href="https://github.com/adrianolimagarcia/HAOS"><img src="https://img.shields.io/badge/Release-2026.9.8-blue?style=for-the-badge" alt="Release Date"></a>
  <a href="https://github.com/adrianolimagarcia/HAOS"><img src="https://img.shields.io/badge/Version-0.21.1-cyan?style=for-the-badge" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://github.com/adrianolimagarcia/HAOS"><img src="https://img.shields.io/badge/Architecture-100%25%20Standalone-purple?style=for-the-badge" alt="Standalone"></a>
</p>

---

## ⚡ O que é o HAOS?

O **HAOS** (*Hermes Agentic Operating System*) é um sistema operacional agêntico de desenvolvimento de software de alta performance e autonomia contínua. Ele combina uma espinha dorsal de orquestração multi-agente via **Kanban**, um motor de auto-evolução (**Ouroboros**), aprendizado contínuo por **Instintos Atômicos com Confidence Scoring**, execução em modo **Ultrawork** e um **Control Plane Web nativo** (porta `8788`).

Projetado sob a filosofia de **Cintura Estreita (Narrow Waist) e Autonomia Máxima**:
- **100% Standalone:** Zero Docker obrigatório, zero Kubernetes, zero bancos externos (Postgres/Redis).
- **Persistência Concorrente Local:** SQLite com WAL (*Write-Ahead Logging*) para Kanban, eventos e métricas.
- **Cache de Prompt Sagrado:** Prompt de sistema estável a nível de byte; injeção dinâmica segura no payload de ferramentas (`role: tool`).
- **Pronto para Offline:** Pode operar 100% desconectado da internet utilizando modelos locais (Ollama, LM Studio, llama-server) ou provedores de nuvem (DeepSeek, OpenRouter, Anthropic, OpenAI).

---

## 🚀 Instalação Rápida (1 Comando)

### Linux e WSL2 (Ubuntu / Debian / Arch / CachyOS / Fedora / macOS)

Execute no terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/adrianolimagarcia/hermes-agent/haos-standalone/scripts/install_haos.sh | bash
```

*(Se já tiver o repositório clonado localmente, basta rodar `./scripts/install_haos.sh`).*

O instalador automático:
1. Instala pacotes essenciais do sistema (`git`, `curl`, `socat`, compiladores).
2. Provisiona o `uv` e cria um ambiente virtual isolado Python 3.11.
3. Instala todas as dependências do HAOS e registra o executável global **`haos`** no seu terminal.
4. Cria o diretório de dados em `~/.haos`.

---

## 🖥️ Começando a Usar

Após a instalação, abra um novo terminal e use o comando **`haos`**:

```bash
# Chat interativo com o agente
haos chat

# Modo ULTRAWORK (autonomia contínua e foco total até testes passarem)
haos chat -u "Construa o módulo de autenticação JWT e execute os testes"

# Status e diagnóstico completo do sistema
haos status

# Gestão do Kanban de tarefas multi-agente
haos kanban list
haos kanban create "Refatorar camada de banco de dados" -u

# Iniciar o Servidor Web do Control Plane (porta 8788)
python scripts/serve_controlplane.py
```

---

## 🌐 Control Plane WebUI (Porta 8788)

O HAOS traz um servidor HTTP nativo e autônomo. Acesse pelo navegador:

👉 **`http://localhost:8788/`** ou **`http://localhost:8788/chat`**

Abas e funcionalidades disponíveis na interface:
- **Console / Chat (`/chat`):** Terminal de missões com streaming em tempo real e despacho nas lanes do agente.
- **Taskboard:** Kanban interativo em tempo real (Ready, Running, Review, Done) com criação e limpeza de tarefas.
- **Team Graph:** Visualização em árvore do grafo cognitivo de subagentes e delegações.
- **Scheduler:** Gestor de tarefas com agendamento dinâmico e grafos de dependência DAG.
- **Ouroboros:** Ledger de auto-evolução com análise de histórico e raio de impacto (*Blast Radius*).
- **Terminal Web:** Shell PTY emulado no navegador para monitoramento e intervenções operacionais.

---

## 🏛️ Recursos Arquiteturais

### 1. Modo Ultrawork (`ulw`)
Ativado via flag `-u` / `--ultrawork`. O agente opera em regime de alta autonomia:
- Não devolve a palavra ao operador com perguntas intermediárias desnecessárias.
- Decompõe autonomamente em (1) exploração via LSP, (2) implementação e (3) execução real de testes.
- **Critério de conclusão:** Uma tarefa de código só é finalizada quando os testes automatizados passarem 100% verdes no terminal.
- Auto-correção (*self-healing*) imediata em caso de erro.

### 2. Aprendizado Contínuo por Instintos Atômicos (ECC-inspired)
- **Instintos Atômicos:** Regras e aprendizados curtos de 1 linha gerados em sessão (`instinct_manage`).
- **Confidence Scoring:** Começa com confiança `0.3`, ganha `+0.2` a cada revalidação bem-sucedida e sofre penalidade em caso de erro.
- **Isolamento por Projeto:** Aprendizados de um projeto em Python não contaminam repositórios em TypeScript ou Rust.
- **Promoção no Ouroboros:** Quando um instinto atinge confiança $\ge 0.8$, o motor de auto-evolução o promove automaticamente a uma **Skill permanente**.
- **Rotina Dream:** Consolidação e auditoria git de memórias via `haos memory dream`.

### 3. Regras Modulares por Caminho (`.haos/rules/*.md`)
- Em vez de inflar arquivos de regras gigantes, as instruções podem ser organizadas em `.haos/rules/*.md`.
- Suporte a frontmatter de ativação sob demanda:
  ```yaml
  ---
  paths:
    - "hermes_cli/**/*.py"
    - "agent/*.py"
  ---
  # Regras do CLI
  Sempre valide novos comandos com a flag --json antes de comitar.
  ```
- Carregamento *lazy* apenas quando o agente acessa arquivos correspondentes, economizando tokens e preservando o cache de prompt.

### 4. Fatiamento Vertical de Tarefas (Vertical PRD Slices)
- Decomposição no Kanban em fatias verticais autônomas (Model/Schema + Lógica de Negócio + Testes Automatizados) via `DAGWorkflowBuilder.add_vertical_slice()`.
- Evita código bloqueado ou intestável até as fases finais do projeto.

### 5. Loop Hygiene Guard & Segurança
- **RepeatToolGuard:** Monitora assinaturas criptográficas SHA-256 das ferramentas e argumentos executados.
- Detecta loops infinitos ou repetições infrutíferas precocemente (aviso progressivo no 2º ciclo e bloqueio crítico no 4º ciclo).

### 6. Federação de Subagentes Heterogêneos
- Suporte nativo a subagentes **DSH** (*DeepSeek Harness*), **ACP** e **Codex** coordenados diretamente pelo Kanban do HAOS via flag `--harness`.

---

## 🛠️ Matriz de Comandos CLI

| Comando | Descrição |
| :--- | :--- |
| `haos chat` | Abre o chat conversacional interativo com o agente |
| `haos chat -u "missão"` | Executa uma missão em modo autônomo **Ultrawork** |
| `haos kanban list` | Lista as tarefas e lanes do Task Engine |
| `haos kanban create "título" -u` | Cria uma nova tarefa com loop de metas e testes |
| `haos evolution status` | Exibe o status e propostas de auto-evolução do Ouroboros |
| `haos evolution analyze` | Analisa métricas e promove instintos confiáveis a Skills |
| `haos evolution blast-radius <arquivos>` | Calcula o raio de impacto de alterações no código |
| `haos memory dream` | Executa a rotina de consolidação e versionamento de memória |
| `haos status` | Exibe o MOTD com diagnóstico completo dos módulos do HAOS |

---

## 🧪 Testes e Qualidade

O HAOS possui uma suíte com mais de 39.000 testes com isolamento estrito de subprocessos. Para rodar a validação das suítes do HAOS:

```bash
./scripts/run_tests.sh \
  tests/test_haos_agentic_engineering.py \
  tests/test_haos_ultrawork.py \
  tests/test_haos_instincts.py \
  tests/test_haos_dynamic_forms.py \
  tests/test_haos_durable_workflow.py \
  tests/test_haos_dsh_tool.py \
  tests/test_haos_loop_hygiene.py \
  tests/test_haos_lsp_tool.py \
  tests/test_haos_external_worker.py \
  tests/test_haos_workspace_scope.py \
  tests/test_haos_session_mention.py \
  tests/test_haos_dream_memory.py \
  tests/agent/test_subdirectory_hints.py
```

---

## 📄 Licença

Distribuído sob a licença MIT. Veja `LICENSE` para mais informações.
