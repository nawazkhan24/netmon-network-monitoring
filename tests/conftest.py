import os
import tempfile

# must be set BEFORE the app is imported, so tests never touch the real database
_tmp = tempfile.mkdtemp()
os.environ["NETMON_DB"] = os.path.join(_tmp, "test.db")
os.environ["FAIL_THRESHOLD"] = "2"

import pytest  # noqa: E402

from app.database import db, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    init_db()
    with db() as conn:
        for table in ("alert_state", "alerts", "metrics", "devices"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence")  # ids start from 1 again
    yield
