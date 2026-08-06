"""tests/api/conftest.py stubs several app.* modules process-wide (sys.modules).

These tests import the real render path and the real ComfyUI client, so purge
any stubbed app modules before this directory's test modules import them — the
same guard tests/spooler uses. Already-imported test modules keep their bound
references, so this does not affect tests collected earlier.
"""
import sys

for _mod in [k for k in list(sys.modules) if k == "app" or k.startswith("app.")]:
    del sys.modules[_mod]
