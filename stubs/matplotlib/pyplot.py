"""See matplotlib/__init__.py - importable, inert."""

from . import _Stub

_impl = _Stub()


def __getattr__(name):
    return getattr(_impl, name)
