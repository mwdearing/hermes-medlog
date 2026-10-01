"""medlog: a deterministic medication log. See medlog.cli for the command line."""
__version__ = "0.1.3"

from . import core as _core  # noqa: E402
from . import cli as _cli  # noqa: E402

# Flat access (medlog.main, medlog.store_for, ...) as the single-file module offered.
for _mod in (_core, _cli):
    for _name, _value in vars(_mod).items():
        if not _name.startswith("__") and _name not in ("annotations",):
            globals().setdefault(_name, _value)
del _mod, _name, _value
