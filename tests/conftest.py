from __future__ import annotations

import os
import tempfile

# Never let tests attach the JMM operational logger to logs/collection.log.
# Child processes inherit this too, so indirect runner.main() tests stay isolated.
_TEST_LOG_DIR = tempfile.mkdtemp(prefix="jmm-pytest-")
os.environ["JMM_COLLECTION_LOG_PATH"] = os.path.join(_TEST_LOG_DIR, "collection.log")
