"""
tests/_minimal_pytest_runner.py

NOT part of the deliverable test suite. This sandbox has neither `pytest`
nor `torch` installed and no network access to install either (see
docs/lstm_model.md). tests/test_lstm_model.py is written against real
pytest (fixtures, `pytest.importorskip`) and should be run with
`python -m pytest tests/test_lstm_model.py -v` in a normal environment.

This tiny shim exists only so the tokenizer tests -- which need neither
library -- could actually be executed and their real pass/fail output
captured honestly in this session, instead of just asserting "the tests
would pass" without evidence. It provides just enough of a `pytest` stub
(`importorskip`, a `tmp_path`-like fixture) to run the test functions
directly; it is not a pytest reimplementation and should not be used for
anything beyond this one-off verification run.
"""
from __future__ import annotations

import re
import sys
import tempfile
import traceback
import types
from pathlib import Path


class _Skipped(Exception):
    pass


def _importorskip(name: str):
    try:
        import importlib
        return importlib.import_module(name)
    except ImportError:
        raise _Skipped(f"module '{name}' not installed in this environment")


class _RaisesContext:
    def __init__(self, expected_exception, match=None):
        self.expected_exception = expected_exception
        self.match = match

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.expected_exception}")
        if not issubclass(exc_type, self.expected_exception):
            return False  # re-raise, wrong exception type
        if self.match is not None and not re.search(self.match, str(exc_val)):
            raise AssertionError(f"Exception {exc_val!r} did not match {self.match!r}")
        return True  # suppress the exception -- it matched


def _raises(expected_exception, match=None):
    return _RaisesContext(expected_exception, match=match)


class _MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value=None):
        if value is None and isinstance(target, str):
            # "module.attr"-style string form -- not used by our tests, skip.
            raise NotImplementedError("string-form monkeypatch.setattr not needed here")
        old = getattr(target, name)
        self._undo.append((target, name, old))
        setattr(target, name, value)

    def undo(self):
        for target, name, old in reversed(self._undo):
            setattr(target, name, old)
        self._undo.clear()


def _skip(reason: str = ""):
    raise _Skipped(reason)


def _fixture(*args, **kwargs):
    if args and callable(args[0]):
        return args[0]
    return lambda fn: fn


fake_pytest = types.ModuleType("pytest")
fake_pytest.importorskip = _importorskip
fake_pytest.raises = _raises
fake_pytest.skip = _skip
fake_pytest.fixture = _fixture
sys.modules.setdefault("pytest", fake_pytest)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def run_module(mod, results):
    for name in dir(mod):
        if not name.startswith("test_"):
            continue
        fn = getattr(mod, name)
        if not callable(fn):
            continue
        mp = _MonkeyPatch()
        try:
            params = fn.__code__.co_varnames[: fn.__code__.co_argcount]
            kwargs = {}
            if "tmp_path" in params:
                kwargs["tmp_path"] = Path(tempfile.mkdtemp())
            if "monkeypatch" in params:
                kwargs["monkeypatch"] = mp
            fn(**kwargs)
            results["passed"].append(f"{mod.__name__}::{name}")
        except _Skipped as e:
            results["skipped"].append((f"{mod.__name__}::{name}", str(e)))
        except Exception:
            results["failed"].append((f"{mod.__name__}::{name}", traceback.format_exc()))
        finally:
            mp.undo()


results = {"passed": [], "failed": [], "skipped": []}

import test_lstm_model as mod1  # noqa: E402
run_module(mod1, results)

import test_tokenizer_streaming as mod2  # noqa: E402
run_module(mod2, results)

import test_sanitize_training_pairs as mod3  # noqa: E402
run_module(mod3, results)

import test_build_prefix_index as mod4  # noqa: E402
run_module(mod4, results)

import test_lstm_inference as mod5  # noqa: E402
run_module(mod5, results)

import test_api as mod6  # noqa: E402
run_module(mod6, results)

# NOTE: tests/test_prefix_index.py uses a session-scoped pytest fixture
# (`built_index`) that this tiny shim does not implement fixture-injection
# for, so it is NOT run through this runner. It was instead verified
# separately by calling build_index_from_records() directly against the
# synthetic corpus after the index_builder.py progress-logging edit (see
# the session transcript / docs/prefix_index.md) -- same stats as before
# the edit, confirming no behavior change. Run it for real with:
#   python -m pytest tests/test_prefix_index.py -v

print(f"PASSED: {len(results['passed'])}")
for n in results["passed"]:
    print(f"  ok   {n}")
print(f"SKIPPED: {len(results['skipped'])}")
for n, reason in results["skipped"]:
    print(f"  skip {n} -- {reason}")
print(f"FAILED: {len(results['failed'])}")
for n, tb in results["failed"]:
    print(f"  FAIL {n}")
    print(tb)

sys.exit(1 if results["failed"] else 0)
