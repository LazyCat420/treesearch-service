import asyncio
import inspect
import os
import pytest

# Monkeypatch inspect.findsource to handle virtualenv absolute path mismatches pointing to rods-project
_orig_findsource = inspect.findsource
def _patched_findsource(obj):
    try:
        return _orig_findsource(obj)
    except OSError as e:
        try:
            file = inspect.getfile(obj)
            if "/rods-project/" in file:
                new_file = file.replace("/rods-project/", "/projects/")
                if os.path.exists(new_file):
                    _orig_getfile = inspect.getfile
                    _orig_getsourcefile = inspect.getsourcefile
                    inspect.getfile = lambda o: new_file
                    inspect.getsourcefile = lambda o: new_file
                    try:
                        return _orig_findsource(obj)
                    finally:
                        inspect.getfile = _orig_getfile
                        inspect.getsourcefile = _orig_getsourcefile
        except Exception:
            pass
        raise e

inspect.findsource = _patched_findsource


@pytest.fixture(scope="session")
def event_loop():
    """Create a single session-scoped event loop to prevent event loop mismatch errors in async tests."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()
