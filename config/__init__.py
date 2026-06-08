"""Project configuration package.

The single source of truth for every tunable parameter lives in
:mod:`config.config`. Import it as::

    from config import config
    if config.ENABLE_SYNC:
        ...

Nothing else in the codebase should read ``os.environ`` directly for
configuration — change a value in ``config/config.py`` (or, optionally,
override via an environment variable of the same name for deployment).
"""

from config import config  # re-export so ``from config import config`` always works

__all__ = ["config"]
