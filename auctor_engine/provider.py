from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol


class WritingProvider(Protocol):
    """Minimal provider interface.

    The engine is provider-neutral. A provider receives a system instruction,
    a user payload, and a JSON schema. It must return a JSON-compatible mapping.
    """

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: Mapping[str, object],
    ) -> Mapping[str, object]: ...


@dataclass
class CallbackProvider:
    callback: Callable[[str, str, Mapping[str, object]], Mapping[str, object]]

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self.callback(system, user, schema)
