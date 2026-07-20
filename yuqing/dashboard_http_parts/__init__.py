# -*- coding: utf-8 -*-
"""Internal building blocks for the dashboard HTTP compatibility adapter."""

from .auth import AuthFlowMixin
from .responses import ResponseSupportMixin
from .router import dispatch_get, dispatch_post, dispatch_put

__all__ = [
    "AuthFlowMixin",
    "ResponseSupportMixin",
    "dispatch_get",
    "dispatch_post",
    "dispatch_put",
]
