"""Manifesto de superfícies: o que o otimizador PODE editar e o que é read-only.

WHY: o loop de auto-melhoria do HAOS aceita a nota que ele mesmo informa
(``ouroboros_lifecycle.evaluate_proposal``, que só compara os dois scores passados pelo
chamador) e, sem runner registrado, aprova a skill com
``spec.eval_score if not None else 1.0`` (``procedural_engine.evaluate_and_activate``).
Enquanto o placar for um arquivo em disco, um otimizador com escrita no lugar errado não
precisa melhorar nada: ele reescreve a régua (os casos de eval), o juiz (o verifier), a
configuração do modelo que o julga, ou o registro do que aconteceu. Este módulo declara,
num só lugar e com o PORQUÊ de cada linha, o que é saída legítima de um otimizador e o que
ele não pode tocar. O porquê importa mais que a linha: um manifesto sem motivo é o
primeiro a ser "ajustado" quando incomoda.

Não é sandbox de filesystem. As tools de arquivo já têm seus guards
(``tools/file_tools_write_guards.py`` — caminhos sensíveis, escopo de workspace,
arquivos de instrução). Aqui a pergunta é outra e mais estreita: *quais superfícies
são produto de um otimizador agendado* (o curador em ``agent/curator.py``, o motor
procedural, qualquer variante futura). A guarda vale para quem a chama — um humano
ou um teste editando ``evals/`` não passa por aqui.

Semântica de decisão: a superfície mais específica (caminho mais longo) vence; em
empate, read-only vence. Caminho não declarado NÃO é gravável — o otimizador só
escreve onde o manifesto diz que a escrita é o produto dele. Declarar uma superfície
nova é uma decisão consciente, e é esse o ponto.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

# Ancoragens: "state" = HERMES_HOME (perfil ativo), "root" = raiz Hermes
# compartilhada entre perfis (kanban é compartilhado por design, ver
# kanban_db.kanban_home), "repo" = árvore de instalação deste checkout.
ANCHOR_STATE = "state"
ANCHOR_ROOT = "root"
ANCHOR_REPO = "repo"

PathLike = Union[str, os.PathLike]


class ReadOnlySurfaceError(PermissionError):
    """Escrita recusada porque o alvo cai numa superfície read-only (ou não declarada)."""


@dataclass(frozen=True)
class Surface:
    """Uma superfície declarada: raiz + se o otimizador pode escrever nela + o porquê."""

    anchor: str
    relpath: str
    writable: bool
    why: str

    def root(self) -> Path:
        """Raiz absoluta resolvida (symlinks seguidos, como os write guards fazem)."""
        if self.anchor == ANCHOR_STATE:
            from hermes_constants import get_hermes_home

            base = get_hermes_home()
        elif self.anchor == ANCHOR_ROOT:
            from hermes_constants import get_default_hermes_root

            base = get_default_hermes_root()
        elif self.anchor == ANCHOR_REPO:
            # .../hermes/platform/evolution/surface_manifest.py -> raiz do checkout.
            base = Path(__file__).resolve().parents[3]
        else:  # pragma: no cover — âncora inválida é erro de declaração, não de runtime
            raise ValueError(f"âncora desconhecida no manifesto de superfícies: {self.anchor!r}")
        return (base / self.relpath).resolve() if self.relpath else Path(base).resolve()

    def label(self) -> str:
        prefix = {ANCHOR_STATE: "<HERMES_HOME>", ANCHOR_ROOT: "<HERMES_ROOT>", ANCHOR_REPO: "<repo>"}[self.anchor]
        return f"{prefix}/{self.relpath}" if self.relpath else prefix


# --- Superfícies EDITÁVEIS: onde a escrita é o produto do otimizador -------------

EDITABLE_SURFACES: Tuple[Surface, ...] = (
    Surface(
        ANCHOR_STATE, "skills", True,
        "skills criadas pelo agente: é o produto do curador (consolida/arquiva em "
        "skills/.archive/, snapshots em skills/.curator_backups/, telemetria em skills/.usage.json). "
        "Bundled, hub e external-dirs já são recusados a montante por skill_usage.archive_skill.",
    ),
    Surface(
        ANCHOR_STATE, "memories", True,
        "memórias do agente: fato aprendido é saída legítima, e o custo de errar é baixo "
        "(não é o placar de ninguém).",
    ),
    Surface(
        ANCHOR_STATE, os.path.join("memory", "skill_archive.db"), True,
        "arquivo de variantes de skill (skill_archive.default_skill_archive_path): registro de "
        "linhagem que o próprio curador escreve ao arquivar/reativar (curator._sync_archive_status). "
        "Fica em memory/ (stores do perfil: instincts, ragflow.db, ...), NÃO em memories/ "
        "(MEMORY.md/USER.md) — só este arquivo é editável ali; o resto de memory/ segue negado "
        "por padrão.",
    ),
    Surface(
        ANCHOR_STATE, "logs", True,
        "logs e relatórios do curador (logs/curator/, agent/curator.py::_reports_root): saída sobre o que "
        "aconteceu, não a evidência que decide.",
    ),
    Surface(
        ANCHOR_STATE, os.path.join("cron", "jobs.json"), True,
        "jobs de cron: o curador reescreve referências de skill depois de consolidar, senão o job "
        "roda sem instruções (curator._rewrite_cron_refs -> cron.jobs.rewrite_skill_refs).",
    ),
    Surface(
        ANCHOR_ROOT, "kanban.db", True,
        "board default do kanban (kanban_db.kanban_db_path): tarefa é saída do agente. "
        "HERMES_KANBAN_HOME pode re-apontar o board; o manifesto declara a intenção, não o env.",
    ),
    Surface(
        ANCHOR_ROOT, "kanban", True,
        "boards nomeados, anexos e workspaces do kanban (kanban_db.boards_root).",
    ),
)

# --- Superfícies READ-ONLY: a régua, o juiz e o registro ------------------------

READ_ONLY_SURFACES: Tuple[Surface, ...] = (
    Surface(
        ANCHOR_REPO, "evals", False,
        "os casos de avaliação e o split held-out (evals/holdout_split.py): quem otimiza não edita "
        "a régua que o mede.",
    ),
    Surface(
        ANCHOR_REPO, os.path.join("hermes", "platform", "evals"), False,
        "harness de eval e golden tasks: a mesma régua, em outro diretório.",
    ),
    Surface(
        ANCHOR_REPO, os.path.join("hermes", "platform", "evolution", "surface_manifest.py"), False,
        "este manifesto: auto-proteção. O otimizador não reescreve a regra que o restringe.",
    ),
    Surface(
        ANCHOR_REPO, os.path.join("hermes", "platform", "evolution", "ouroboros_lifecycle.py"), False,
        "o gate que julga a proposta (ouroboros_lifecycle.evaluate_proposal): é o verifier do ciclo.",
    ),
    Surface(
        ANCHOR_REPO, os.path.join("hermes", "platform", "evolution", "ledger.py"), False,
        "o registro append-only das decisões de evolução: reescrevê-lo apaga a evidência.",
    ),
    Surface(
        ANCHOR_REPO, os.path.join("hermes", "platform", "skills"), False,
        "motor procedural: o gate de ativação de skill (procedural_engine.evaluate_and_activate).",
    ),
    Surface(
        ANCHOR_REPO, os.path.join("hermes", "platform", "observability"), False,
        "event store/replay: o stream do que aconteceu, base de qualquer comparação entre execuções.",
    ),
    Surface(
        ANCHOR_REPO, os.path.join("agent", "verification_evidence.py"), False,
        "o ledger de evidência de verificação (agent/verification_evidence.py, store em _db_path): diz o que foi "
        "de fato provado.",
    ),
    Surface(
        ANCHOR_REPO, "agent", False,
        "núcleo do agente — loop, prompt e o próprio curador (agent/curator.py) que executa a poda: "
        "editar aqui deixa o otimizador reescrever quem o julga.",
    ),
    Surface(
        ANCHOR_REPO, "tools", False,
        "implementação das tools (skill_manage, ledger de skills, guards de escrita): editar aqui "
        "deixa o otimizador forjar o próprio registro.",
    ),
    Surface(
        ANCHOR_REPO, "tests", False,
        "a suíte que prova o comportamento: teste que o otimizador edita não prova nada.",
    ),
    Surface(
        ANCHOR_REPO, os.path.join("scripts", "run_tests.sh"), False,
        "o runner canônico dos testes (isola HERMES_HOME, TZ e locale): é o how do verifier.",
    ),
    Surface(
        ANCHOR_STATE, "config.yaml", False,
        "configuração de modelo/provedor: o otimizador não escolhe — nem rebaixa — o modelo que o "
        "julga.",
    ),
    Surface(
        ANCHOR_STATE, "state.db", False,
        "store de sessões/mensagens: o histórico pelo qual o agente é julgado.",
    ),
    Surface(
        ANCHOR_STATE, "verification_evidence.db", False,
        "o banco do ledger de evidência de verificação: mesma razão de agent/verification_evidence.py.",
    ),
    Surface(
        ANCHOR_STATE, ".env", False,
        "segredos: credencial é do usuário, não do otimizador.",
    ),
    Surface(
        ANCHOR_STATE, "auth.json", False,
        "credenciais OAuth: o otimizador não troca de identidade para trocar de juiz.",
    ),
)

ALL_SURFACES: Tuple[Surface, ...] = EDITABLE_SURFACES + READ_ONLY_SURFACES


@dataclass(frozen=True)
class SurfaceDecision:
    """Resultado da classificação de um caminho, com o motivo legível da decisão."""

    path: Path
    writable: bool
    surface: Optional[Surface]
    reason: str


def _resolve(path: PathLike) -> Path:
    """Absoluto + symlinks seguidos, mesmo quando o arquivo ainda não existe."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def classify(path: PathLike) -> SurfaceDecision:
    """Decide se o otimizador pode escrever em *path*, com o porquê da resposta."""
    target = _resolve(path)
    matches: List[Tuple[Surface, Path]] = []
    for surface in ALL_SURFACES:
        root = surface.root()
        if target == root or root in target.parents:
            matches.append((surface, root))
    if not matches:
        return SurfaceDecision(
            path=target,
            writable=False,
            surface=None,
            reason=(
                f"superfície não declarada: {target} — o otimizador só escreve nas superfícies "
                "editáveis do manifesto (hermes/platform/evolution/surface_manifest.py); "
                "se a escrita é produto legítimo do otimizador, declare-a lá com o porquê"
            ),
        )
    # Mais específico vence; em empate, read-only vence (negar é o lado seguro).
    surface, root = max(matches, key=lambda item: (len(str(item[1])), not item[0].writable))
    kind = "editável" if surface.writable else "read-only"
    return SurfaceDecision(
        path=target,
        writable=surface.writable,
        surface=surface,
        reason=f"superfície {kind} {surface.label()}: {surface.why}",
    )


def is_writable(path: PathLike) -> bool:
    """True só quando *path* cai numa superfície editável declarada."""
    return classify(path).writable


def assert_writable(path: PathLike) -> Path:
    """Devolve o caminho resolvido quando a escrita é permitida; senão levanta
    ``ReadOnlySurfaceError`` com o motivo (superfície + porquê) já pronto para o log."""
    decision = classify(path)
    if not decision.writable:
        raise ReadOnlySurfaceError(f"escrita recusada em {decision.path}: {decision.reason}")
    return decision.path


def render_manifest() -> str:
    """Manifesto em texto — para o relatório de uma rodada e para leitura humana."""
    def rows(title: str, surfaces: Tuple[Surface, ...]) -> List[str]:
        return [f"{title} ({len(surfaces)})"] + [
            f"  {'✎' if s.writable else '🔒'} {s.label():<44} {s.why}" for s in surfaces
        ]

    return "\n".join(
        rows("Superfícies editáveis pelo otimizador", EDITABLE_SURFACES)
        + [""]
        + rows("Superfícies read-only", READ_ONLY_SURFACES)
        + ["", "Não declarada = não gravável (negação por padrão); superfície mais específica vence."]
    )
