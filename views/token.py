
# -*- coding: utf-8 -*-

import urllib.request, urllib.error, urllib.parse
import os, sys, datetime, argparse, re
import subprocess
import base64
import shlex
import logging
import socket
from sys import argv
import constants

# 認証リクエストのタイムアウト(秒)。未指定だと応答がないまま待ち続けるため必ず付ける
AUTH_TIMEOUT_SEC = 15

_log = logging.getLogger("%s.%s" % (constants.LOG_PREFIX, "Token"))


def _open(req, step):
    """認証リクエストを送り、失敗時は原因をログに残して再送出する（トークン等の値は記録しない）"""
    try:
        return urllib.request.urlopen(req, timeout=AUTH_TIMEOUT_SEC)
    except urllib.error.HTTPError as e:
        _log.warning("%s failed: HTTP %s", step, e.code)
        raise
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        _log.warning("%s failed: %r", step, e)
        raise


class Token:
    auth_token = ""
    auth_key = "bcd151073c03b352e1ef2fd66c32209da9ca0afa" ## 迴ｾ迥ｶ縺ｯ蝗ｺ螳・key_lenght = 0
    key_offset = 0

    def __init__(self):
        self.area = None

    def auth1(self):
        url = "https://radiko.jp/v2/api/auth1"
        headers = {}
        auth_response = {}

        headers = {
            "User-Agent": constants.BROWSER_USER_AGENT,
            "Accept": "*/*",
            "X-Radiko-App":"pc_html5" ,
            "X-Radiko-App-Version":"0.0.1" ,
            "X-Radiko-User":"dummy_user" ,
            "X-Radiko-Device":"pc" ,
        }
        req = urllib.request.Request( url, None, headers  )
        res = _open(req, "auth1")
        auth_response["body"] = res.read()
        auth_response["headers"] = res.info()
        #print(auth_response)
        return auth_response

    def get_partial_key(self, auth_response):
        try:
            authtoken = auth_response["headers"]["x-radiko-authtoken"]
            offset    = int(auth_response["headers"]["x-radiko-keyoffset"])
            length    = int(auth_response["headers"]["x-radiko-keylength"])
        except (KeyError, TypeError, ValueError) as e:
            _log.error("auth1 response is missing auth headers: %r", e)
            raise RuntimeError("radiko の認証応答が不正です。") from e
        if not authtoken:
            _log.error("auth1 response has empty auth token")
            raise RuntimeError("radiko の認証応答が不正です。")
        partialkey= self.auth_key[offset:offset+length]
        partialkey = base64.b64encode(partialkey.encode())

        # logging.info(f"authtoken: {authtoken}")
        # logging.info(f"offset: {offset}")
        # logging.info(f"length: {length}")
        # logging.info(f"partialkey: {partialkey}")

        return [partialkey,authtoken]

    def auth2(self, partialkey, auth_token, radiko_session=None) :
        url = "https://radiko.jp/v2/api/auth2"
        if radiko_session:
            # プレミアム会員セッションを付けるとエリアフリーの権限が付与される
            url += "?" + urllib.parse.urlencode({"radiko_session": radiko_session})
        headers =  {
            "User-Agent": constants.BROWSER_USER_AGENT,
            "X-Radiko-AuthToken": auth_token,
            "X-Radiko-Partialkey": partialkey,
            "X-Radiko-User": "dummy_user",
            "X-Radiko-Device": 'pc' # 'pc' 蝗ｺ螳・
        }
        req  = urllib.request.Request( url, None, headers  )
        res  = _open(req, "auth2 (premium)" if radiko_session else "auth2")
        txt = res.read()
        self.area = txt.decode()
        _log.debug("auth2 succeeded: area=%s", self.area.split(",")[0].strip())
        return self.area

