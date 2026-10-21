# -*- coding: utf-8 -*-
# radiko プレミアム（エリアフリー）会員セッション管理

import base64
import threading
from logging import getLogger

import requests
import constants

LOGIN_URL = "https://radiko.jp/v4/api/member/login"
LOGOUT_URL = "https://radiko.jp/v4/api/member/logout"

CONFIG_SECTION = "premium"


class PremiumAuthError(Exception):
    """ログイン失敗。kind で原因を区別する"""
    INVALID_CREDENTIALS = "invalid_credentials"
    NOT_AREAFREE = "not_areafree"
    NETWORK = "network"

    def __init__(self, kind, message=""):
        super().__init__(message or kind)
        self.kind = kind


class PremiumSession:
    """プロセス全体で共有するプレミアム会員セッション。

    ProgramManager はアプリ内の各所で個別に生成されるため、
    セッションはこのシングルトンに保持して auth2 から参照する。
    """

    def __init__(self):
        self.log = getLogger("%s.%s" % (constants.LOG_PREFIX, "PremiumSession"))
        self._lock = threading.Lock()
        self._radiko_session = None
        self._areafree = False
        self._mail = None
        self._password = None

    def login(self, mail, password):
        """ログインしてエリアフリー契約を検証する。失敗時は PremiumAuthError"""
        try:
            response = requests.post(
                LOGIN_URL,
                data={"mail": mail, "pass": password},
                timeout=15,
            )
        except requests.RequestException as e:
            self.log.warning("premium login request failed: %s", e)
            raise PremiumAuthError(PremiumAuthError.NETWORK, str(e))

        if response.status_code in (400, 401, 403):
            self.log.info("premium login rejected: status=%s", response.status_code)
            raise PremiumAuthError(PremiumAuthError.INVALID_CREDENTIALS)
        if not response.ok:
            self.log.warning("premium login failed: status=%s", response.status_code)
            raise PremiumAuthError(PremiumAuthError.NETWORK, "HTTP %s" % response.status_code)

        try:
            data = response.json()
        except ValueError:
            self.log.warning("premium login response is not json")
            raise PremiumAuthError(PremiumAuthError.NETWORK, "invalid response")

        radiko_session = str(data.get("radiko_session") or "").strip()
        areafree = str(data.get("areafree") or "").strip() == "1"
        if not radiko_session:
            raise PremiumAuthError(PremiumAuthError.INVALID_CREDENTIALS)
        if not areafree:
            # 無料会員/タイムフリー30のみ等。セッションは使わないので破棄する
            self.log.info("premium login succeeded but areafree is not enabled")
            self._post_logout(radiko_session)
            raise PremiumAuthError(PremiumAuthError.NOT_AREAFREE)

        with self._lock:
            old_session = self._radiko_session
            self._radiko_session = radiko_session
            self._areafree = True
            self._mail = mail
            self._password = password
        if old_session and old_session != radiko_session:
            self._post_logout(old_session)
        self.log.info("premium login succeeded (areafree enabled)")
        return True

    def relogin(self):
        """保持している認証情報で再ログインする。成功すれば True"""
        with self._lock:
            mail, password = self._mail, self._password
        if not mail or not password:
            return False
        try:
            return self.login(mail, password)
        except PremiumAuthError as e:
            self.log.warning("premium relogin failed: %s", e.kind)
            if e.kind != PremiumAuthError.NETWORK:
                self.invalidate()
            return False

    def logout(self):
        with self._lock:
            radiko_session = self._radiko_session
            self._radiko_session = None
            self._areafree = False
            self._mail = None
            self._password = None
        if radiko_session:
            self._post_logout(radiko_session)
            self.log.info("premium logout")

    def invalidate(self):
        """セッションを無効化する（サーバーへのログアウトは行わない）"""
        with self._lock:
            self._radiko_session = None
            self._areafree = False

    def get_session(self):
        with self._lock:
            return self._radiko_session

    def is_areafree(self):
        with self._lock:
            return bool(self._radiko_session) and self._areafree

    def _post_logout(self, radiko_session):
        try:
            requests.post(LOGOUT_URL, data={"radiko_session": radiko_session}, timeout=10)
        except requests.RequestException as e:
            self.log.debug("premium logout request failed: %s", e)


session = PremiumSession()


def _protect(text):
    """DPAPI で現在の Windows ユーザーに紐づけて暗号化する"""
    import win32crypt
    blob = win32crypt.CryptProtectData(text.encode("utf-8"), None, None, None, None, 0)
    return base64.b64encode(blob).decode("ascii")


def _unprotect(text):
    import win32crypt
    blob = base64.b64decode(text.encode("ascii"))
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode("utf-8")


def save_credentials(config, mail, password):
    config[CONFIG_SECTION]["mail"] = mail
    config[CONFIG_SECTION]["password"] = _protect(password)
    config[CONFIG_SECTION]["autoLogin"] = True


def clear_credentials(config):
    config[CONFIG_SECTION]["mail"] = ""
    config[CONFIG_SECTION]["password"] = ""
    config[CONFIG_SECTION]["autoLogin"] = False


def load_credentials(config):
    """保存済みの (mail, password) を返す。なければ None"""
    if not config.getboolean(CONFIG_SECTION, "autoLogin", False):
        return None
    mail = config[CONFIG_SECTION]["mail"]
    encrypted = config[CONFIG_SECTION]["password"]
    if not mail or not encrypted:
        return None
    try:
        return mail, _unprotect(encrypted)
    except Exception as e:
        getLogger("%s.%s" % (constants.LOG_PREFIX, "PremiumSession")).warning(
            "failed to decrypt saved premium credentials: %s", e
        )
        return None


def auto_login(config):
    """保存済み認証情報で自動ログインする。失敗しても例外は投げない"""
    credentials = load_credentials(config)
    if not credentials:
        return False
    try:
        return session.login(*credentials)
    except PremiumAuthError as e:
        getLogger("%s.%s" % (constants.LOG_PREFIX, "PremiumSession")).warning(
            "premium auto login failed: %s", e.kind
        )
        return False
