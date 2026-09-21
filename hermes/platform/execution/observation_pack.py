"""ObservationPack: Contenção Eficiente de Logs e Saídas de Terminal (Padrão SoL-Pi da NVIDIA).

Arquiva saídas volumosas em disco (/tmp/haos_logs/) e entrega ao contexto
do modelo apenas um excerto cirúrgico (head + tail) acompanhado do handle do arquivo.
Evita explosão de tokens e preserva prompt caching.
"""

from pathlib import Path
import time
from typing import Optional, Tuple


class ObservationPack:
    MAX_LINES: int = 60
    MAX_CHARS: int = 4000
    HEAD_LINES: int = 15
    TAIL_LINES: int = 25
    DEFAULT_LOG_DIR: Path = Path("/tmp/haos_logs")

    @classmethod
    def pack(
        cls,
        output: str,
        identifier: str = "run",
        base_dir: Optional[Path] = None,
        max_lines: Optional[int] = None,
        max_chars: Optional[int] = None,
    ) -> Tuple[str, Optional[str], bool]:
        """Avalia se 'output' ultrapassa os limites seguros.

        Se ultrapassar:
            - Salva o output integral em /tmp/haos_logs/<identifier>_<ts>.log
            - Retorna (excerto_com_handle, caminho_do_arquivo, True)
        Se couber nos limites:
            - Retorna (output_original, None, False)
        """
        if not output:
            return output, None, False

        limit_lines = max_lines or cls.MAX_LINES
        limit_chars = max_chars or cls.MAX_CHARS

        lines = output.splitlines()
        total_lines = len(lines)
        total_chars = len(output)

        if total_lines <= limit_lines and total_chars <= limit_chars:
            return output, None, False

        # Fast-Path Rust Nativo (hermes-exec pack-output): zero-copy de heap no Python
        rust_bin = "/usr/local/bin/hermes-exec"
        if not Path(rust_bin).exists():
            import os
            candidate = os.path.join(os.getcwd(), "target/release/hermes-exec")
            if Path(candidate).exists():
                rust_bin = candidate

        if Path(rust_bin).exists():
            try:
                import json, subprocess
                cmd = [
                    rust_bin,
                    "pack-output",
                    "-",
                    identifier,
                    str(base_dir or cls.DEFAULT_LOG_DIR),
                    str(limit_lines),
                    str(limit_chars),
                ]
                proc = subprocess.run(
                    cmd,
                    input=output,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    fast_res = json.loads(proc.stdout)
                    if fast_res.get("truncated"):
                        return fast_res.get("text", output), fast_res.get("file_path"), True
            except Exception:
                pass

        # Cria diretório de logs se não existir (Fallback Python)
        log_dir = base_dir or cls.DEFAULT_LOG_DIR
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in identifier)[:32]
            timestamp = int(time.time() * 1000)
            log_file = log_dir / f"{safe_id}_{timestamp}.log"
            log_file.write_text(output, encoding="utf-8", errors="replace")
            file_path_str = str(log_file)
        except Exception:
            file_path_str = None

        # Monta excerto cirúrgico (head + tail)
        head_count = cls.HEAD_LINES
        tail_count = cls.TAIL_LINES

        if total_lines > (head_count + tail_count):
            head_part = "\n".join(lines[:head_count])
            tail_part = "\n".join(lines[-tail_count:])
            omitted_lines = total_lines - head_count - tail_count
        else:
            # Muitos caracteres concentrados em poucas linhas: split por caracteres
            half = limit_chars // 2
            head_part = output[: int(half * 0.4)]
            tail_part = output[-int(half * 0.6) :]
            omitted_lines = 0

        handle_note = (
            f"\n\n[... ObservationPack (SoL-Pi): {total_lines:,} linhas / {total_chars:,} caracteres. "
            f"Omitidos {omitted_lines:,} linhas do miolo. "
        )
        if file_path_str:
            handle_note += (
                f"Log completo salvo em: {file_path_str} — "
                f"Use a ferramenta `read` se precisar inspecionar partes específicas ...]\n\n"
            )
        else:
            handle_note += "Log truncado para proteger a janela de contexto ...]\n\n"

        packaged_text = head_part + handle_note + tail_part
        return packaged_text, file_path_str, True
