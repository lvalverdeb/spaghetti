"""Architecture contracts: parsing, evaluation, and the workspace check."""

from __future__ import annotations

from pathlib import Path

import pytest

import spaghetti.config as _cfg
from spaghetti import detector as ds
from spaghetti.contracts import Contract, ContractError, parse_contracts, violation_reason
from spaghetti.models import ScanConfig
from spaghetti.settings import SettingsError, parse_mapping

FORBIDDEN = Contract(
    "no fw",
    "forbidden",
    source=("shop.domain",),
    forbidden=("shop.transport", "fastapi"),
    allow=("fastapi.encoders",),
)
LAYERS = Contract("layering", "layers", layers=("shop.api", "shop.service", "shop.domain"))


@pytest.mark.parametrize(
    ("contract", "module", "target", "expected"),
    [
        (
            FORBIDDEN,
            "shop.domain.order",
            "shop.transport.http",
            "'shop.transport' is forbidden for 'shop.domain.order'",
        ),
        (
            FORBIDDEN,
            "shop.domain.order",
            "fastapi",
            "'fastapi' is forbidden for 'shop.domain.order'",
        ),
        (FORBIDDEN, "shop.domain.order", "fastapi.encoders", None),  # allow-listed
        (FORBIDDEN, "shop.domain.order", "shop.domain.money", None),
        (FORBIDDEN, "shop.api.routes", "shop.transport.http", None),  # not a source module
        (FORBIDDEN, "shop.domainx", "fastapi", None),  # prefix match is on dotted segments
        (
            LAYERS,
            "shop.domain.order",
            "shop.api.routes",
            "layer 'shop.domain' may not depend on the higher layer 'shop.api'",
        ),
        (LAYERS, "shop.service.checkout", "shop.domain.order", None),
        (LAYERS, "shop.api.routes", "shop.api.deps", None),  # same layer
        (LAYERS, "shop.api.routes", "requests", None),  # outside every layer
    ],
)
def test_violation_reason(contract, module, target, expected):
    assert violation_reason(contract, module, target) == expected


def test_parse_contracts_validates_shape():
    good = [
        {"name": "a", "type": "forbidden", "source": ["x"], "forbidden": ["y"]},
        {"name": "b", "type": "layers", "layers": ["x.api", "x.core"]},
    ]
    parsed = parse_contracts(good, "pyproject.toml")
    assert [c.kind for c in parsed] == ["forbidden", "layers"]
    assert Contract.from_json(parsed[0].to_json()) == parsed[0]
    for bad, match in [
        ("nope", "must be a list"),
        ([{"type": "forbidden"}], "name is required"),
        ([{"name": "a", "type": "deny"}], "type must be one of"),
        ([{"name": "a", "type": "forbidden", "source": ["x"]}], "needs source and forbidden"),
        ([{"name": "a", "type": "layers", "layers": ["x"]}], "at least two layers"),
        ([{"name": "a", "type": "layers", "layers": ["x", "y"], "bogus": 1}], "unknown key"),
        (
            [{"name": "a", "type": "forbidden", "source": "x", "forbidden": ["y"]}],
            "list of non-empty strings",
        ),
    ]:
        with pytest.raises(ContractError, match=match):
            parse_contracts(bad, "pyproject.toml")


def test_settings_carry_contracts(tmp_path: Path):
    s = parse_mapping(
        {"contracts": [{"name": "a", "type": "forbidden", "source": ["x"], "forbidden": ["y"]}]},
        origin=tmp_path / "pyproject.toml",
    )
    assert s.contracts[0].name == "a"
    with pytest.raises(SettingsError, match="contracts\\[1\\]"):
        parse_mapping({"contracts": [{"name": "a"}]}, origin=tmp_path / "pyproject.toml")


def test_contract_violation_reported_from_the_workspace_pass(tmp_path: Path, monkeypatch):
    root = tmp_path / "shop"
    (root / "domain").mkdir(parents=True)
    (root / "transport").mkdir()
    (root / "__init__.py").write_text("")
    (root / "domain" / "__init__.py").write_text("")
    (root / "domain" / "order.py").write_text(
        "import json\nfrom shop.transport.http import Client\nfrom fastapi import FastAPI\n"
    )
    (root / "transport" / "__init__.py").write_text("")
    (root / "transport" / "http.py").write_text("from shop.domain.order import Order\n")
    monkeypatch.setattr(ds, "PACKAGES", {"shop": root})
    monkeypatch.setattr(
        _cfg,
        "CONTRACTS",
        [
            Contract(
                "domain stays framework-free",
                "forbidden",
                source=("shop.domain",),
                forbidden=("shop.transport", "fastapi"),
            ),
            Contract("layering", "layers", layers=("shop.transport", "shop.domain")),
        ],
    )
    config = ScanConfig(exclude=[], min_duplicate_lines=5, twin_similarity=0.6)
    per = {"shop": ds.scan_package("shop", root, config=config)}
    ds.scan_workspace(per, config=config)
    violations = [i for i in per["shop"].issues if i.rule == "contract-violation"]
    assert [(i.file.name, i.line) for i in violations] == [
        ("order.py", 2),
        ("order.py", 2),
        ("order.py", 3),
    ]
    assert (
        "Contract 'domain stays framework-free': shop.domain.order imports shop.transport.http"
        in violations[0].message
    )
    assert (
        "layer 'shop.domain' may not depend on the higher layer 'shop.transport'"
        in violations[1].message
    )
    assert all(i.severity == "error" and i.fingerprint for i in violations)
