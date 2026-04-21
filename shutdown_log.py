# -*- coding: utf-8 -*-
"""Shutdown-sequence logging helpers (no dependency on AppBase / views.base / app)."""
import logging

import constants


def get_shutdown_logger():
    return logging.getLogger(f"{constants.LOG_PREFIX}.Shutdown")


def flush_app_log_handlers():
    """Flush buffered records to RADAR.log."""
    for h in logging.getLogger(constants.LOG_PREFIX).handlers:
        try:
            h.flush()
        except Exception:
            pass
