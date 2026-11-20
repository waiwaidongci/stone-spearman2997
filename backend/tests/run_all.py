"""Run every backend test: python -m tests.run_all  (from backend/).

Each module gets a fresh interpreter because they configure independent
SQLite databases through environment variables at import time.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = ['tests.test_segmentation', 'tests.test_worker_resume', 'tests.test_services', 'tests.test_api']

failed = []
for name in MODULES:
    result = subprocess.run([sys.executable, '-m', name], cwd=ROOT)
    if result.returncode != 0:
        failed.append(name)
print('\nFAILED: ' + ', '.join(failed) if failed else '\nALL TESTS PASSED')
sys.exit(1 if failed else 0)
