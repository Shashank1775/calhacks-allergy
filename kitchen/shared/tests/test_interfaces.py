"""Drift check: the Protocols in kitchen/shared/interfaces.py match the python blocks in contracts/apis.md.

Owner: platform (agents)
Step:  shared
Reads: contracts/apis.md, kitchen.shared.interfaces
Emits: nothing
Rules: signatures match contracts/apis.md exactly; a method or parameter added on one side only fails here
"""

import ast
import dataclasses
import inspect
import re

import pytest

from kitchen.shared import interfaces
from kitchen.shared.models import CONTRACTS_DIR


def apis_md_classes():
    """Every class defined in a ```python block of contracts/apis.md, by name."""
    text = (CONTRACTS_DIR / "apis.md").read_text()
    classes = {}
    for block in re.findall(r"```python\n(.*?)```", text, re.DOTALL):
        for node in ast.parse(block).body:
            if isinstance(node, ast.ClassDef):
                classes[node.name] = node
    return classes


def is_protocol(node):
    """True when the class in apis.md is declared `class X(Protocol)`."""
    return any(isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases)


def contract_methods(node):
    """Method name -> parameter names (positional, then keyword-only, without self) for a class in apis.md."""
    return {
        item.name: [arg.arg for arg in item.args.args[1:]] + [arg.arg for arg in item.args.kwonlyargs]
        for item in node.body
        if isinstance(item, ast.FunctionDef)
    }


def contract_attributes(node):
    """Annotated attribute names (`name: str`) of a class in apis.md."""
    return [item.target.id for item in node.body if isinstance(item, ast.AnnAssign)]


def implemented_methods(cls):
    """Method name -> parameter names (without self) for a class in interfaces.py."""
    return {
        name: [parameter for parameter in inspect.signature(member).parameters if parameter != "self"]
        for name, member in vars(cls).items()
        if inspect.isfunction(member) and not name.startswith("_")
    }


CONTRACT_CLASSES = apis_md_classes()
CONTRACT_PROTOCOLS = sorted(name for name, node in CONTRACT_CLASSES.items() if is_protocol(node))


def test_interfaces_defines_exactly_the_protocols_in_apis_md():
    implemented = sorted(
        name
        for name, member in vars(interfaces).items()
        if inspect.isclass(member)
        and member.__module__ == interfaces.__name__
        and getattr(member, "_is_protocol", False)
    )
    assert implemented == CONTRACT_PROTOCOLS


@pytest.mark.parametrize("name", CONTRACT_PROTOCOLS)
def test_each_protocol_has_exactly_the_methods_and_parameters_in_apis_md(name):
    assert implemented_methods(getattr(interfaces, name)) == contract_methods(CONTRACT_CLASSES[name])


@pytest.mark.parametrize("name", CONTRACT_PROTOCOLS)
def test_each_protocol_has_exactly_the_attributes_in_apis_md(name):
    assert list(inspect.get_annotations(getattr(interfaces, name))) == contract_attributes(CONTRACT_CLASSES[name])


def test_pair_window_has_exactly_the_fields_in_apis_md():
    fields = [field.name for field in dataclasses.fields(interfaces.PairWindow)]
    assert fields == contract_attributes(CONTRACT_CLASSES["PairWindow"])


def test_the_drift_check_sees_every_interface_in_apis_md():
    """Guards the parser above: if it stopped finding the blocks, every other test here would pass vacuously."""
    assert "StateClient" in CONTRACT_PROTOCOLS
    assert len(CONTRACT_PROTOCOLS) == 10
    assert "PairWindow" in CONTRACT_CLASSES
