"""Minimal matplotlib stand-in for the AirTouch .app bundle.

MediaPipe's drawing helpers import matplotlib.pyplot at import time but never
call it unless you use those helpers. Shipping real matplotlib costs ~50 MB and
a font-cache build on first launch, so the frozen app ships this stub.
"""

__version__ = "0.0.0-airtouch-stub"


class _Stub:
    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            return None

        return _noop

    def __call__(self, *args, **kwargs):
        return None


def use(*args, **kwargs):
    return None
