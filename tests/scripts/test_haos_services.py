"""Contratos da unidade systemd que o instalador HAOS provisiona.

Contexto (por que este módulo existe): o controlplane era o único componente sem
gerador de unidade — o instalador o subia com ``nohup`` + PID file, e um reboot
levava o serviço embora. Os contratos abaixo são os que fazem a unidade valer
para os dois layouts de instalação (root e usuário) sem nunca escrever em lugar
nenhum quando não há systemd para receber a unidade.
"""

from pathlib import Path

from scripts import haos_services as svc


def test_system_unit_uses_the_paths_it_was_given(tmp_path):
    """Os caminhos vêm de argumento, não de constante: o layout root do
    instalador (``/usr/local/lib/haos-agent`` + ``/root/.haos``) e o de usuário
    (``~/.local/share/haos-agent`` + ``~/.haos``) usam a MESMA função. A unidade
    à mão que esta substitui tinha caminho fixo e PATH de uma árvore inexistente.
    """
    install_dir = tmp_path / "opt-haos-agent"
    haos_home = tmp_path / "state-haos"
    python = install_dir / "venv" / "bin" / "python"

    unit = svc.render_controlplane_unit(
        install_dir=install_dir,
        haos_home=haos_home,
        python=python,
        run_as_user="haos",
        system=True,
    )

    assert f"ExecStart={python} {install_dir}/scripts/serve_controlplane.py" in unit
    assert f"WorkingDirectory={install_dir}" in unit
    assert f'Environment="HAOS_HOME={haos_home}"' in unit
    # A casa de estado e a do agente apontam para o MESMO lugar: separá-las é o
    # que produzia duas bases de sessão na máquina migrada.
    assert f'Environment="HERMES_HOME={haos_home}"' in unit
    assert "User=haos" in unit
    assert "Restart=always" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "/usr/local/lib/haos-agent" not in unit  # nada de caminho fixo


def test_user_scope_and_safety_of_dry_run(tmp_path, monkeypatch):
    """Escopo de usuário não declara ``User=`` nem pede alvo de sistema, e um
    host sem systemd (Termux/container) não pode acabar com arquivo escrito.
    """
    unit = svc.render_controlplane_unit(
        install_dir="/home/u/.local/share/haos-agent",
        haos_home="/home/u/.haos",
        python="/home/u/.local/share/haos-agent/venv/bin/python",
        system=False,
    )
    assert "User=" not in unit
    assert "WantedBy=default.target" in unit

    target_dir = tmp_path / "units"
    path = svc.install_controlplane_unit(
        install_dir="/x/haos-agent",
        haos_home="/x/.haos",
        python="/x/haos-agent/venv/bin/python",
        unit_dir=target_dir,
        dry_run=True,
    )
    assert path == target_dir / svc.UNIT_NAME
    assert not target_dir.exists()  # dry-run não toca o disco

    monkeypatch.setattr(svc.shutil, "which", lambda _name: None)
    assert svc.systemctl_available(system=True) is False
    assert svc.systemctl_available(system=False) is False
