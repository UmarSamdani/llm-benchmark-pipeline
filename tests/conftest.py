import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each Lambda lives in its own directory and they all define app.py, so the
# directories are added to sys.path under distinct names by the loaders in
# the individual test modules. This file only sets the environment that the
# modules read at import time.

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")

os.environ.setdefault("BUCKET_NAME", "test-bucket")
os.environ.setdefault("TABLE_NAME", "test-table")

sys.path.insert(0, str(ROOT))
