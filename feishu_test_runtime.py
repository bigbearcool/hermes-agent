"""Local-only shared-PM-interpreter lexical stdlib allowance; no state allowance."""
import sysconfig
import os
import pytest

@pytest.fixture(autouse=True)
def _allow_interpreter_lexical_stdlib(monkeypatch):
    from tests import home_io_guard
    prefix = os.path.normcase(sysconfig.get_path('stdlib'))
    monkeypatch.setattr(home_io_guard, '_INTERPRETER_PREFIX_STRS',
                        (*home_io_guard._INTERPRETER_PREFIX_STRS, prefix))
    from pathlib import Path
    manifest_probe = str(Path(__file__).parent.parent / 'manifest.json')
    original = home_io_guard.HomeIOGuard.check
    import sys
    interpreter_metadata = {os.path.normcase(value) for value in sys.path
                            if value.endswith('.zip')}
    def check(self, value, **kwargs):
        if kwargs.get('metadata') and isinstance(value, (str, Path)) and (
            os.fspath(value) == manifest_probe or os.path.normcase(os.fspath(value)) in interpreter_metadata
        ):
            return
        return original(self, value, **kwargs)
    monkeypatch.setattr(home_io_guard.HomeIOGuard, 'check', check)
