from abc import ABC, abstractmethod


class BaseService(ABC):
    """Base service abstraction."""

    @abstractmethod
    def run(self) -> None:
        raise NotImplementedError
