import asyncio
import pytest

@pytest.fixture(scope="session")
def event_loop():
    """Create a single session-scoped event loop to prevent event loop mismatch errors in async tests."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()
