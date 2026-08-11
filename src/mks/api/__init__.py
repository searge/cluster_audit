"""HTTP delivery layer.

A sibling of ``mks.cli``, not a new architectural layer: the dependency
direction is the same (api -> application, api -> config), and everything here
is adapters — routes in, application functions out. Serving logic belongs in
``mks.application``.
"""

from mks.api.capacity import create_app

__all__ = ["create_app"]
