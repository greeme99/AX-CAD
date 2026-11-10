"""plan §22 W0: the automation coverage scripts still read the code they measure."""

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "tools/coverage"


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lisp_coverage_counts_the_interpreter():
    r = load("lisp_coverage").classify()
    assert len(r["done"]) >= 200 and "setq" in r["done"] and "entmake" in r["done"]
    assert "vl-registry-read" in r["excluded"]


def test_activex_coverage_finds_the_object_model():
    m = load("activex_coverage")
    have = m.implemented_members()
    assert {"addline", "startpoint", "getpoint"} <= have
    assert "%" in m.report()


def test_dotnet_coverage_reads_the_sdk():
    m = load("dotnet_coverage")
    assert {"Transaction", "Line"} <= m.sdk_types()
    assert "core set" in m.report()
