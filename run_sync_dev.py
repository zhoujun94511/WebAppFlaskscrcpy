"""Local preview launcher.

The feature switches and all parameters live in ``config/config.py`` now —
this wrapper only forces preview-friendly *binding* (localhost, fixed port,
no auto-opened browser) for the in-IDE preview, then runs app.py as __main__.
For normal use just run ``python app.py``; edit ``config/config.py`` to
configure anything.
"""

import os

# Preview binds localhost so the embedded browser can reach it on a fixed port.
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "5001")
os.environ.setdefault("OPEN_BROWSER", "0")

import runpy

runpy.run_path("app.py", run_name="__main__")
