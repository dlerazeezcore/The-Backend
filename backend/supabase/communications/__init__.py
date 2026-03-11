"""Supabase communications repositories."""

from .email_config_repo import load_email_config_doc, save_email_config_doc

__all__ = ["load_email_config_doc", "save_email_config_doc"]
