"""HAOS Doctor — diagnóstico de integridade de ambiente, caminhos e permissões.

Valida em tempo de execução:
1. Variáveis de ambiente (HAOS_HOME, HERMES_HOME, HAOS_DATA_DIR)
2. Permissões de arquivos e segredos (~/.haos/.env em 0600)
3. Integridade do config.yaml
4. Integridade dos bancos de dados canônicos (kanban.db, events.db)
5. Isolamento contra vazamentos para ~/.hermes
6. Disponibilidade de executáveis de lanes (haos-agent, haos, hermes)
7. Status das portas de rede (8788 e 9191)
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class CheckResult:
    def __init__(self, name: str, status: str, message: str, details: Optional[Dict[str, Any]] = None):
        self.name = name
        self.status = status  # "PASS", "WARN", "FAIL"
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


class HAOSDoctor:
    """Motor de diagnóstico e inspeção de saúde do HAOS."""

    def __init__(self, haos_home: Optional[Path] = None):
        if haos_home is not None:
            self.haos_home = Path(haos_home).expanduser()
        else:
            self.haos_home = Path(os.environ.get("HAOS_HOME", Path.home() / ".haos")).expanduser()

    def run_all_checks(self) -> Dict[str, Any]:
        """Executa todas as verificações diagnósticas e retorna relatório estruturado."""
        results: List[CheckResult] = []

        results.append(self.check_haos_home())
        results.append(self.check_env_isolation())
        results.append(self.check_secrets_permissions())
        results.append(self.check_config_integrity())
        results.append(self.check_data_persistence())
        results.append(self.check_databases_integrity())
        results.append(self.check_runtimes_and_lanes())
        results.append(self.check_network_ports())

        passed = sum(1 for r in results if r.status == "PASS")
        warned = sum(1 for r in results if r.status == "WARN")
        failed = sum(1 for r in results if r.status == "FAIL")

        overall_status = "HEALTHY" if failed == 0 and warned == 0 else ("DEGRADED" if failed == 0 else "UNHEALTHY")

        return {
            "overall_status": overall_status,
            "haos_home": str(self.haos_home),
            "summary": {"total": len(results), "passed": passed, "warned": warned, "failed": failed},
            "checks": [r.to_dict() for r in results],
        }

    def check_haos_home(self) -> CheckResult:
        """Verifica se HAOS_HOME existe e possui permissões de leitura/escrita."""
        if not self.haos_home.exists():
            return CheckResult(
                name="haos_home_exists",
                status="FAIL",
                message=f"Diretório HAOS_HOME '{self.haos_home}' não existe.",
                details={"path": str(self.haos_home)},
            )

        if not os.access(self.haos_home, os.R_OK | os.W_OK):
            return CheckResult(
                name="haos_home_permissions",
                status="FAIL",
                message=f"Sem permissão de leitura/escrita em '{self.haos_home}'.",
                details={"path": str(self.haos_home)},
            )

        # Probe de escrita temporária
        probe = self.haos_home / ".doctor_probe"
        try:
            probe.write_text("probe", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return CheckResult(
                name="haos_home",
                status="PASS",
                message=f"HAOS_HOME '{self.haos_home}' operacional com permissões válidas.",
                details={"path": str(self.haos_home)},
            )
        except Exception as exc:
            return CheckResult(
                name="haos_home_write",
                status="FAIL",
                message=f"Falha ao escrever em '{self.haos_home}': {exc}",
                details={"path": str(self.haos_home), "error": str(exc)},
            )

    def check_env_isolation(self) -> CheckResult:
        """Verifica se HERMES_HOME está alinhado ou se há contaminação cruzada."""
        hermes_home = os.environ.get("HERMES_HOME")
        haos_home_str = str(self.haos_home.resolve())

        if hermes_home:
            hh_resolved = str(Path(hermes_home).expanduser().resolve())
            if hh_resolved != haos_home_str and ".hermes" in hh_resolved:
                return CheckResult(
                    name="env_isolation",
                    status="WARN",
                    message=(
                        f"HERMES_HOME ({hh_resolved}) aponta para .hermes em vez de HAOS_HOME ({haos_home_str}). "
                        "Pode haver leitura de configurações upstream."
                    ),
                    details={"HERMES_HOME": hermes_home, "HAOS_HOME": haos_home_str},
                )

        return CheckResult(
            name="env_isolation",
            status="PASS",
            message="Variáveis de ambiente isoladas ou compatíveis com HAOS_HOME.",
            details={"HAOS_HOME": haos_home_str, "HERMES_HOME": hermes_home},
        )

    def check_secrets_permissions(self) -> CheckResult:
        """Verifica permissões do arquivo .env (ideal 0600)."""
        env_file = self.haos_home / ".env"
        if not env_file.exists():
            return CheckResult(
                name="secrets_permissions",
                status="WARN",
                message=f"Arquivo de segredos '{env_file}' não encontrado.",
                details={"path": str(env_file)},
            )

        mode = env_file.stat().st_mode
        perms = stat.S_IMODE(mode)
        octal_perms = oct(perms)

        # Se for legível por grupo ou outros (mask 0077)
        if perms & 0o077:
            return CheckResult(
                name="secrets_permissions",
                status="WARN",
                message=f"Permissões inseguras em '{env_file}': {octal_perms}. Recomendado: 0600.",
                details={"path": str(env_file), "permissions": octal_perms},
            )

        return CheckResult(
            name="secrets_permissions",
            status="PASS",
            message=f"Arquivo de segredos '{env_file}' protegido com permissões restritas ({octal_perms}).",
            details={"path": str(env_file), "permissions": octal_perms},
        )

    def check_config_integrity(self) -> CheckResult:
        """Verifica integridade do config.yaml."""
        config_file = self.haos_home / "config.yaml"
        if not config_file.exists():
            return CheckResult(
                name="config_integrity",
                status="WARN",
                message=f"Arquivo '{config_file}' não encontrado. Serão utilizados valores padrão.",
                details={"path": str(config_file)},
            )

        try:
            content = config_file.read_text(encoding="utf-8")
            import yaml  # noqa: PLC0415
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                return CheckResult(
                    name="config_integrity",
                    status="WARN",
                    message=f"Conteúdo de '{config_file}' não é um dicionário YAML válido.",
                    details={"path": str(config_file)},
                )
            return CheckResult(
                name="config_integrity",
                status="PASS",
                message=f"Configuração '{config_file}' válida ({len(data)} seções encontradas).",
                details={"path": str(config_file), "sections": list(data.keys())},
            )
        except Exception as exc:
            return CheckResult(
                name="config_integrity",
                status="FAIL",
                message=f"Erro de sintaxe em '{config_file}': {exc}",
                details={"path": str(config_file), "error": str(exc)},
            )

    def check_data_persistence(self) -> CheckResult:
        """Verifica que os dados estão isolados e não em ~/.hermes/haos."""
        legacy_dir = Path.home() / ".hermes" / "haos"
        has_legacy = legacy_dir.exists() and any(legacy_dir.iterdir()) if legacy_dir.exists() else False

        data_dir = Path(os.environ.get("HAOS_DATA_DIR", self.haos_home))
        if has_legacy:
            return CheckResult(
                name="data_persistence_isolation",
                status="WARN",
                message=(
                    f"Detectados dados residuais em '{legacy_dir}'. "
                    f"O HAOS agora utiliza '{data_dir}' para persistência isolada."
                ),
                details={"data_dir": str(data_dir), "legacy_dir": str(legacy_dir)},
            )

        return CheckResult(
            name="data_persistence_isolation",
            status="PASS",
            message=f"Persistência isolada em '{data_dir}', sem poluição em ~/.hermes.",
            details={"data_dir": str(data_dir)},
        )

    def check_databases_integrity(self) -> CheckResult:
        """Verifica integridade dos bancos SQLite do HAOS."""
        data_dir = Path(os.environ.get("HAOS_DATA_DIR", self.haos_home))
        dbs = ["kanban.db", "events.db"]
        checked: List[str] = []

        for db_name in dbs:
            db_path = data_dir / db_name
            if db_path.exists() and db_path.is_file():
                try:
                    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                    cur = conn.cursor()
                    cur.execute("PRAGMA integrity_check;")
                    res = cur.fetchone()
                    conn.close()
                    if res and res[0] == "ok":
                        checked.append(f"{db_name} (ok)")
                    else:
                        return CheckResult(
                            name="databases_integrity",
                            status="FAIL",
                            message=f"Banco de dados corrompido: {db_path}",
                            details={"db": str(db_path), "result": res},
                        )
                except Exception as exc:
                    return CheckResult(
                        name="databases_integrity",
                        status="FAIL",
                        message=f"Falha ao validar integridade de {db_path}: {exc}",
                        details={"db": str(db_path), "error": str(exc)},
                    )

        if not checked:
            return CheckResult(
                name="databases_integrity",
                status="PASS",
                message="Bancos de dados canônicos ainda não inicializados (serão criados na primeira tarefa).",
                details={"checked": []},
            )

        return CheckResult(
            name="databases_integrity",
            status="PASS",
            message=f"Integridade SQLite confirmada: {', '.join(checked)}.",
            details={"checked": checked},
        )

    def check_runtimes_and_lanes(self) -> CheckResult:
        """Verifica a presença e prontidão dos executáveis agênticos."""
        from hermes.platform.execution.lane_executor import HermesCliLaneWorker

        worker = HermesCliLaneWorker()
        resolved_cmd = worker.hermes_command
        is_available = worker.available()

        if not resolved_cmd or not is_available:
            return CheckResult(
                name="lane_runtime",
                status="WARN",
                message=(
                    "Runtime agêntico local não resolvido. Tarefas em lanes locais podem requerer "
                    "HAOS_HERMES_COMMAND ou PATH configurado."
                ),
                details={"resolved_command": resolved_cmd, "available": is_available},
            )

        return CheckResult(
            name="lane_runtime",
            status="PASS",
            message=f"Runtime agêntico operacional: '{resolved_cmd}'.",
            details={"resolved_command": resolved_cmd, "available": is_available},
        )

    def check_network_ports(self) -> CheckResult:
        """Verifica disponibilidade das portas de serviço."""
        ports = {"ControlPlane": 8788, "HermesDashboard": 9191}
        statuses = {}

        for name, port in ports.items():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            try:
                s.bind(("127.0.0.1", port))
                s.close()
                statuses[name] = f"Porta {port} livre"
            except OSError:
                statuses[name] = f"Porta {port} em uso (serviço ativo ou ocupado)"

        return CheckResult(
            name="network_ports",
            status="PASS",
            message=f"Portas de rede auditadas: {statuses['ControlPlane']} | {statuses['HermesDashboard']}.",
            details=statuses,
        )

    @classmethod
    def print_terminal_report(cls, haos_home: Optional[Path] = None, json_output: bool = False) -> int:
        """Imprime relatório amigável no terminal."""
        doctor = cls(haos_home)
        report = doctor.run_all_checks()

        if json_output:
            print(json.dumps(report, indent=2))
            return 0 if report["summary"]["failed"] == 0 else 1

        print("=" * 65)
        print("🩺 HAOS DOCTOR — Verificação de Integridade e Isolamento")
        print("=" * 65)
        print(f"Diretório HAOS_HOME: {report['haos_home']}")
        print(f"Status Geral:       {report['overall_status']}")
        print(
            f"Resultados:         {report['summary']['passed']} OK | "
            f"{report['summary']['warned']} Avisos | {report['summary']['failed']} Falhas"
        )
        print("-" * 65)

        for check in report["checks"]:
            st = check["status"]
            icon = "✅ [PASS]" if st == "PASS" else ("⚠️  [WARN]" if st == "WARN" else "❌ [FAIL]")
            print(f"{icon:<10} {check['message']}")

        print("=" * 65)
        return 0 if report["summary"]["failed"] == 0 else 1
