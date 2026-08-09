"""tests/api/conftest.py stubs app.scanner.scanner process-wide (sys.modules).

This directory's tests need the real implementation, so purge any stubbed
app.* modules before importing it — the same guard tests/jobs uses.
"""
import sys

for _mod in [k for k in list(sys.modules) if k == "app" or k.startswith("app.")]:
    del sys.modules[_mod]
