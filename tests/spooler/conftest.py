"""tests/api/conftest.py stubs several app.* modules process-wide (sys.modules).
These tests exercise the REAL spooler, so purge any stubbed app modules before
this directory's test modules import them. Already-imported test modules keep
their bound references, so this does not affect tests collected earlier.
"""
import sys

for _mod in [k for k in list(sys.modules) if k == "app" or k.startswith("app.")]:
    del sys.modules[_mod]
