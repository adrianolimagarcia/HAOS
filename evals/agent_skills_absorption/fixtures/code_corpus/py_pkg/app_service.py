from py_pkg.base import BaseService
from py_pkg.math_service import Calculator, standalone_mult


class AppService(BaseService):
    """Application coordination service."""

    def __init__(self) -> None:
        self.calc = Calculator()

    def run(self) -> None:
        self.execute_flow(10, 20)

    def execute_flow(self, x: int, y: int) -> int:
        sum_res = self.calc.add(x, y)
        mult_res = standalone_mult(sum_res, 2)
        return mult_res


def entrypoint() -> None:
    svc = AppService()
    svc.run()
