# -*- coding: utf-8 -*-
"""Route registry for the versioned dashboard HTTP API."""

from .router import dispatch_get, dispatch_post, dispatch_put

__all__ = ["dispatch_get", "dispatch_post", "dispatch_put"]
