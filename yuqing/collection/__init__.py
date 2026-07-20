# -*- coding: utf-8 -*-
"""Internal collection pipeline boundaries.

Public compatibility remains in :mod:`yuqing.collect`.  Modules in this
package separate external fetching, semantic relevance, per-platform
persistence, and watch-level orchestration.
"""

from .fetchers import OPENCLI_SITE

__all__ = ["OPENCLI_SITE"]
