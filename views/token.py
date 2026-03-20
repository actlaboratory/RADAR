
# -*- coding: utf-8 -*-

import urllib.request, urllib.error, urllib.parse
import os, sys, datetime, argparse, re
import subprocess
import base64
import shlex
import logging
from sys import argv


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
            "User-Agent": "curl/7.56.1",
            "Accept": "*/*",
            "X-Radiko-App":"pc_html5" ,
            "X-Radiko-App-Version":"0.0.1" ,
            "X-Radiko-User":"dummy_user" ,
            "X-Radiko-Device":"pc" ,
        }
        req = urllib.request.Request( url, None, headers  )
        res = urllib.request.urlopen(req)
        auth_response["body"] = res.read()
        auth_response["headers"] = res.info()
        #print(auth_response)
        return auth_response

    def get_partial_key(self, auth_response):
        authtoken = auth_response["headers"]["x-radiko-authtoken"]
        offset    = auth_response["headers"]["x-radiko-keyoffset"]
        length    = auth_response["headers"]["x-radiko-keylength"]
        offset = int(offset)
        length = int(length)
        partialkey= self.auth_key[offset:offset+length]
        partialkey = base64.b64encode(partialkey.encode())

        # logging.info(f"authtoken: {authtoken}")
        # logging.info(f"offset: {offset}")
        # logging.info(f"length: {length}")
        # logging.info(f"partialkey: {partialkey}")

        return [partialkey,authtoken]

    def auth2(self, partialkey, auth_token ) :
        url = "https://radiko.jp/v2/api/auth2"
        headers =  {
            "X-Radiko-AuthToken": auth_token,
            "X-Radiko-Partialkey": partialkey,
            "X-Radiko-User": "dummy_user",
            "X-Radiko-Device": 'pc' # 'pc' 蝗ｺ螳・
        }
        req  = urllib.request.Request( url, None, headers  )
        res  = urllib.request.urlopen(req)
        txt = res.read()
        self.area = txt.decode()
        return self.area

    def gen_temp_chunk_m3u8_url(self, url, auth_token ):
        headers =  {
            "X-Radiko-AuthToken": auth_token,
        }
        req  = urllib.request.Request( url, None, headers  )
        res  = urllib.request.urlopen(req)
        body = res.read().decode()
        lines = re.findall( '^https?://.+m3u8$' , body, flags=(re.MULTILINE) )
        # embed()
        return lines[0]

    def gen_timefree_chunk_m3u8_url(self, station_id, ft, to, auth_token):
        """タイムフリー再生用のチャンクm3u8 URLを生成"""
        query = urllib.parse.urlencode({
            "station_id": station_id,
            "ft": ft,
            "to": to,
            "l": "15",
        })
        candidates = [
            f"https://radiko.jp/v2/api/ts/playlist.m3u8?{query}",
            f"http://radiko.jp/v2/api/ts/playlist.m3u8?{query}",
        ]
        headers = {
            "X-Radiko-AuthToken": auth_token,
            "User-Agent": "curl/7.56.1",
            "X-Radiko-App": "pc_html5",
            "X-Radiko-App-Version": "0.0.1",
            "X-Radiko-User": "dummy_user",
            "X-Radiko-Device": "pc",
        }
        last_error = None
        for url in candidates:
            try:
                req = urllib.request.Request(url, None, headers)
                res = urllib.request.urlopen(req)
                body = res.read().decode(errors="ignore")
                lines = re.findall(r'^https?://.+m3u8$', body, flags=(re.MULTILINE))
                if lines:
                    return lines[0]
                # 相対パスや改行形式の揺れに備える
                alt_lines = [
                    line.strip() for line in body.splitlines()
                    if line.strip().endswith(".m3u8")
                ]
                if alt_lines:
                    line = alt_lines[0]
                    if line.startswith("http://") or line.startswith("https://"):
                        return line
            except Exception as e:
                last_error = e
                continue
        raise RuntimeError(f"Failed to resolve timefree chunk m3u8 URL: {last_error}")

