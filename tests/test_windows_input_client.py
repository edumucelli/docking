"""Client ABI preflight retains raw evidence and accepts only proven AMD64."""

import ast
import ctypes
import json
import textwrap
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def client_preflight():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/windows-input.yml"
    )
    script = (
        workflow.read_text()
        .split("python - <<'PY'\n", 1)[1]
        .split("\n          PY", 1)[0]
    )
    tree = ast.parse(textwrap.dedent(script))
    # Exercise the actual workflow statements with independent native/token
    # boundaries supplied below, without querying a developer's Windows token.
    tree.body = [
        statement
        for statement in tree.body
        if not isinstance(statement, (ast.Import, ast.ImportFrom, ast.FunctionDef))
    ]
    return compile(tree, str(workflow), "exec")


@pytest.fixture
def preflight(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    current = Mock(return_value=55)
    kernel = SimpleNamespace(GetCurrentProcess=current)
    architecture = {
        "wow64": {
            "api": "IsWow64Process2",
            "success": True,
            "process_machine": 0,
            "native_machine": 0xAA64,
        },
        "process_type": {
            "api": "GetProcessInformation",
            "information_class": 9,
            "structure_size": 8,
            "success": True,
            "machine": 0x8664,
            "attributes": 1,
        },
        "machine": 0x8664,
        "source": "GetProcessInformation",
    }
    query = Mock(side_effect=lambda *a, **kw: deepcopy(architecture))
    namespace = {
        "ctypes": SimpleNamespace(
            WinDLL=Mock(return_value=kernel),
            c_void_p=ctypes.c_void_p,
            sizeof=ctypes.sizeof,
        ),
        "json": json,
        "Path": Path,
        "os": SimpleNamespace(environ={"MSYSTEM": "UCRT64"}),
        "sys": SimpleNamespace(
            getwindowsversion=lambda: SimpleNamespace(build=26200, product_type=1)
        ),
        "process_architecture": query,
        "token_snapshot": Mock(
            return_value={"integrity_rid": 12288, "elevated": True, "elevation_type": 1}
        ),
        "uac_snapshot": Mock(return_value={"enable_lua": 1}),
    }
    return SimpleNamespace(
        namespace=namespace,
        architecture=architecture,
        query=query,
        kernel=kernel,
        output=tmp_path / "artifacts/windows/client-environment.json",
    )


def test_client_preflight_accepts_proven_emulated_amd64_with_zero_wow_guest(preflight):
    exec(client_preflight(), preflight.namespace)
    preflight.query.assert_called_once_with(preflight.kernel, 55, details=True)
    facts = json.loads(preflight.output.read_text())
    assert facts["process_machine"] == 0 and facts["native_machine"] == 0xAA64
    assert facts["actual_process_machine"] == 0x8664
    assert facts["process_architecture"]["process_type"]["success"]
    assert facts["token"]["elevated"] and facts["uac"]["enable_lua"] == 1


@pytest.mark.parametrize("actual", [0, 0x14C, 0xAA64, 0xA641, 0xA64E])
def test_client_preflight_rejects_other_abis_but_retains_exact_query_evidence(
    preflight, actual
):
    preflight.architecture["machine"] = actual
    preflight.architecture["process_type"]["machine"] = actual
    with pytest.raises(AssertionError):
        exec(client_preflight(), preflight.namespace)
    facts = json.loads(preflight.output.read_text())
    assert facts["actual_process_machine"] == actual
    assert facts["process_architecture"]["process_type"]["information_class"] == 9


def test_client_query_failure_stays_fatal_with_numeric_error_in_artifact(preflight):
    preflight.architecture["machine"] = None
    preflight.architecture["process_type"] = {
        "api": "GetProcessInformation",
        "information_class": 9,
        "structure_size": 8,
        "success": False,
        "winerror": 87,
    }
    with pytest.raises(AssertionError):
        exec(client_preflight(), preflight.namespace)
    facts = json.loads(preflight.output.read_text())
    assert facts["actual_process_machine"] is None
    assert facts["process_architecture"]["process_type"]["winerror"] == 87


def test_client_raw_query_failure_stays_fatal_before_missing_detail_access(preflight):
    preflight.architecture["wow64"] = {
        "api": "IsWow64Process2",
        "success": False,
        "winerror": 5,
    }
    preflight.architecture["machine"] = None
    del preflight.architecture["process_type"]
    with pytest.raises(AssertionError):
        exec(client_preflight(), preflight.namespace)
    facts = json.loads(preflight.output.read_text())
    assert facts["process_machine"] is None and facts["native_machine"] is None
    assert facts["process_architecture"]["wow64"]["winerror"] == 5


def test_later_token_failure_cannot_erase_already_written_abi_diagnostics(preflight):
    preflight.namespace["token_snapshot"].side_effect = OSError(
        5, "fixture token query"
    )
    with pytest.raises(OSError, match="token query"):
        exec(client_preflight(), preflight.namespace)
    facts = json.loads(preflight.output.read_text())
    assert facts["actual_process_machine"] == 0x8664
    assert facts["process_architecture"]["process_type"]["success"]
