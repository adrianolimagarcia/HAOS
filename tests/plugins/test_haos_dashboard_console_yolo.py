"""Console do plugin de dashboard HAOS: o chat do operador despacha em YOLO.

O shell oficial descobre ``plugins/haos/dashboard/plugin_api.py`` e monta o
``router`` sob ``/api/plugins/haos/``. A rota ``POST /console`` cria a missão
digitada pelo operador e a despacha na lane canônica; como o worker é headless,
ela roda sem portão de aprovação por padrão — mesma política da superfície
standalone (``CHAT_YOLO_DEFAULT``).

O teste executa a rota REAL com um ``HAOSStandaloneState`` em data_dir
temporário e verifica o spec persistido no kanban canônico — sem mocks de store.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from hermes.platform.webui.standalone import HAOSStandaloneState

_PLUGIN_API = (
    Path(__file__).resolve().parents[2] / "plugins" / "haos" / "dashboard" / "plugin_api.py"
)


def _load_plugin_api():
    """Carrega o api exatamente como o shell: importlib por path + exec_module."""
    spec = importlib.util.spec_from_file_location("haos_dashboard_console_yolo", _PLUGIN_API)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestHaosDashboardConsoleYolo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.module = _load_plugin_api()
        if not hasattr(self.module, "post_console"):  # fastapi ausente no host
            self.skipTest("fastapi indisponível: o router do plugin não é montado")
        self.state = HAOSStandaloneState(Path(self.tmp.name))
        self.addCleanup(self.state.kanban.close)
        # Sem tick em background: este teste assere o spec persistido, não a lane.
        self.state.settings["auto_dispatch"] = False
        self.module.get_engine_state = lambda: self.state

    def test_console_route_persists_a_yolo_spec(self):
        result = self.module.post_console({"message": "Missão do console do dashboard"})

        self.assertTrue(result["accepted"])
        spec = self.state.kanban.get_task(result["task_id"])["spec"]
        self.assertTrue(spec["yolo_mode"])
