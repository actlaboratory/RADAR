# -*- coding: utf-8 -*-
# ログ出力用のユーティリティ

import re

_AUTH_TOKEN_PATTERN = re.compile(r"(X-Radiko-AuthToken:\s*)[^,\s\r\n]+", re.IGNORECASE)
_PROXY_CREDENTIAL_PATTERN = re.compile(r"(://)[^/@\s]+@")


def mask_secrets(text):
    """認証トークンとプロキシの認証情報を伏せた文字列を返す"""
    text = _AUTH_TOKEN_PATTERN.sub(r"\1***", str(text))
    return _PROXY_CREDENTIAL_PATTERN.sub(r"\1***@", text)


def mask_command(cmd):
    """コマンド引数のリストから、ログに出せる1行の文字列を作る"""
    return " ".join(mask_secrets(arg) for arg in cmd)
