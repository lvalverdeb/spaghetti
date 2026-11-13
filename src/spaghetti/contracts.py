"""Architecture contracts: the one custom rule most teams actually want.

A contract names module prefixes and states which may not import which —
the ``import-linter`` / ``Tach`` idea, evaluated on the workspace import graph
spaghetti already builds. Two kinds:

- ``forbidden``: modules under any ``source`` prefix must not import anything
  under a ``forbidden`` prefix (third-party names included), except ``allow``.
- ``layers``: an ordered list, highest first. A module may import its own
  layer and lower layers; importing a *higher* layer is a violation.

Configured as a list of tables::

    [[tool.spaghetti.contracts]]
    name = "domain stays framework-free"
    type = "forbidden"
    source = ["shop.domain"]
    forbidden = ["shop.transport", "fastapi", "sqlalchemy"]

    [[tool.spaghetti.contracts]]
    name = "clean layering"
    type = "layers"
    layers = ["shop.api", "shop.service", "shop.domain"]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Contract", "ContractError", "parse_contracts", "violation_reason"]

CONTRACT_KINDS = ("forbidden", "layers")
_KEYS = {"name", "type", "source", "forbidden", "allow", "layers"}


class ContractError(ValueError):
    """A contract table is malformed; the message names the contract and key."""


@dataclass(frozen=True)
class Contract:
    name: str
    kind: str
    source: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    layers: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.kind,
            "source": list(self.source),
            "forbidden": list(self.forbidden),
            "allow": list(self.allow),
            "layers": list(self.layers),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Contract:
        return cls(
            name=data["name"],
            kind=data["type"],
            source=tuple(data.get("source", ())),
            forbidden=tuple(data.get("forbidden", ())),
            allow=tuple(data.get("allow", ())),
            layers=tuple(data.get("layers", ())),
        )


def _under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _layer_of(module: str, layers: tuple[str, ...]) -> int | None:
    """Index of the layer ``module`` belongs to (0 = highest), or ``None``."""
    for index, prefix in enumerate(layers):
        if _under(module, prefix):
            return index
    return None


def violation_reason(contract: Contract, module: str, target: str) -> str | None:
    """Why ``module`` importing ``target`` breaks ``contract``, or ``None`` if it doesn't."""
    if contract.kind == "forbidden":
        if not any(_under(module, s) for s in contract.source):
            return None
        if any(_under(target, a) for a in contract.allow):
            return None
        hit = next((f for f in contract.forbidden if _under(target, f)), None)
        return f"'{hit}' is forbidden for '{module}'" if hit else None
    source_layer = _layer_of(module, contract.layers)
    target_layer = _layer_of(target, contract.layers)
    if source_layer is None or target_layer is None or target_layer >= source_layer:
        return None
    return (
        f"layer '{contract.layers[source_layer]}' may not depend on the higher layer "
        f"'{contract.layers[target_layer]}'"
    )


def _str_list(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ContractError(f"{where} must be a list of non-empty strings")
    return tuple(value)


def parse_contracts(data: Any, origin: str) -> list[Contract]:
    """Validate the ``contracts`` setting (a list of tables)."""
    if not isinstance(data, list):
        raise ContractError(f"{origin}: contracts must be a list of tables")
    contracts: list[Contract] = []
    for number, table in enumerate(data, start=1):
        where = f"{origin}: contracts[{number}]"
        if not isinstance(table, dict):
            raise ContractError(f"{where} must be a table")
        unknown = set(table) - _KEYS
        if unknown:
            raise ContractError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        name = table.get("name")
        if not isinstance(name, str) or not name:
            raise ContractError(f"{where}: name is required")
        kind = table.get("type")
        if kind not in CONTRACT_KINDS:
            raise ContractError(f"{where}: type must be one of {', '.join(CONTRACT_KINDS)}")
        contract = Contract(
            name=name,
            kind=kind,
            source=_str_list(table.get("source"), f"{where}.source"),
            forbidden=_str_list(table.get("forbidden"), f"{where}.forbidden"),
            allow=_str_list(table.get("allow"), f"{where}.allow"),
            layers=_str_list(table.get("layers"), f"{where}.layers"),
        )
        if kind == "forbidden" and not (contract.source and contract.forbidden):
            raise ContractError(f"{where}: a forbidden contract needs source and forbidden")
        if kind == "layers" and len(contract.layers) < 2:
            raise ContractError(f"{where}: a layers contract needs at least two layers")
        contracts.append(contract)
    return contracts
