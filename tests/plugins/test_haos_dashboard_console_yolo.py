"""Console e steer do plugin de dashboard HAOS despacham em YOLO.

O shell oficial descobre ``plugins/haos/dashboard/plugin_api.py`` e monta o
``router`` sob ``/api/plugins/haos/``. As rotas que criam missão a partir de uma
ordem do operador — ``POST /console`` e ``POST /tasks/{id}/steer`` nos modos
``queue``/``interrupt`` — nascem em YOLO (``OPERATOR_YOLO_DEFAULT``): o worker é
headless e um prompt de aprovação ali não tem quem responda.

O teste executa as rotas REAIS com um ``HAOSStandaloneState`` em data_dir
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

    def test_steer_queue_and_interrupt_spawn_yolo_missions(self):
        """Os dois modos que criam missão nova herdam a política do console."""
        target = self.module.post_console({"message": "Missão alvo do steer"})["task_id"]

        for mode in ("queue", "interrupt"):
            with self.subTest(mode=mode):
                result = self.module.post_task_steer(
                    target, {"mode": mode, "message": f"ordem via {mode}"}
                )
                new_id = result.get("queued_task_id") or result.get("new_task_id")
                self.assertIsNotNone(new_id, f"modo {mode} não devolveu o id da missão nova")
                spec = self.state.kanban.get_task(new_id)["spec"]
                self.assertTrue(spec["yolo_mode"], f"modo {mode} criou missão com portão ligado")

    def test_plain_steer_does_not_create_a_mission(self):
        """``steer`` só injeta texto na tarefa em voo — não nasce missão nova."""
        target = self.module.post_console({"message": "Missão alvo do steer puro"})["task_id"]
        before = len(self.state.kanban.list_tasks())

        result = self.module.post_task_steer(target, {"mode": "steer", "message": "siga por aqui"})

        self.assertEqual(result["mode"], "steer")
        self.assertEqual(len(self.state.kanban.list_tasks()), before)
