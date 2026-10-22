# -*- coding: utf-8 -*-
# radiko プレミアム（エリアフリー）会員セッション管理

import base64
import threading
from logging import getLogger

import requests
import constants

LOGIN_URL = "https://radiko.jp/v4/api/member/login"
LOGOUT_URL = "https://radiko.jp/v4/api/member/logout"
# radiko の Web サイトが使うログイン状態の確認 API。Cookie の radiko_session を検証する
LOGIN_CHECK_URL = "https://radiko.jp/ap/member/webapi/v2/member/login/check"
# ブラウザでログインするときに開くページ
WEB_LOGIN_URL = "https://radiko.jp/member/login"

# ログイン方法
METHOD_PASSWORD = "password"
METHOD_BROWSER = "browser"

CONFIG_SECTION = "premium"


class PremiumAuthError(Exception):
    """ログイン失敗。kind で原因を区別する"""
    INVALID_CREDENTIALS = "invalid_credentials"
    NOT_AREAFREE = "not_areafree"
    NETWORK = "network"
    # Cloudflare 等のボット対策で弾かれた。認証情報の誤りとは区別する
    BLOCKED = "blocked"
    # ブラウザでログインしたセッションの期限が切れた（自動では再ログインできない）
    SESSION_EXPIRED = "session_expired"

    def __init__(self, kind, message=""):
        super().__init__(message or kind)
        self.kind = kind


def _is_bot_challenge(response):
    """Cloudflare のチャレンジ・ブロック応答かどうか

    API は本来 JSON を返すため、Cloudflare の印が付いた HTML 応答はブロックとみなす。
    radiko 自体が Cloudflare 経由の場合に備え、Server ヘッダーだけでは判定しない。
    """
    headers = response.headers
    if headers.get("cf-mitigated"):
        return True
    is_html = "text/html" in headers.get("Content-Type", "").lower()
    from_cloudflare = "cloudflare" in headers.get("Server", "").lower() or "cf-ray" in headers
    return is_html and from_cloudflare


def is_areafree_flag(value):
    return str(value).strip().lower() in ("1", "true")


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
        # ログアウトのたびに進める。ログアウト前に始まった再ログインの結果を捨てるために使う
        self._generation = 0
        # ユーザー操作以外（再ログイン失敗）でセッションが無効化されたときに呼ぶコールバック。
        # 認証処理のスレッドから呼ばれるため、UI 操作は呼び出し側で UI スレッドへ回すこと
        self._invalidated_listeners = []

    def add_invalidated_listener(self, listener):
        """listener(error) をセッションが無効化されたときに呼ぶよう登録する

        error は無効化の原因の PremiumAuthError（不明なら None）。
        """
        with self._lock:
            self._invalidated_listeners.append(listener)

    def _notify_invalidated(self, error):
        with self._lock:
            listeners = list(self._invalidated_listeners)
        for listener in listeners:
            try:
                listener(error)
            except Exception as e:
                self.log.warning("premium invalidated listener failed: %s", e)

    def login(self, mail, password, _generation=None, _logout_old=True):
        """ログインしてエリアフリー契約を検証する。失敗時は PremiumAuthError

        _generation を渡した場合、その間にログアウトされていたら結果を破棄して False を返す。
        """
        try:
            response = requests.post(
                LOGIN_URL,
                data={"mail": mail, "pass": password},
                headers={"User-Agent": constants.BROWSER_USER_AGENT},
                timeout=15,
            )
        except requests.RequestException as e:
            self.log.warning("premium login request failed: %s", e)
            raise PremiumAuthError(PremiumAuthError.NETWORK, str(e))

        # 403 等をそのまま認証情報の誤りとして扱わないよう、ステータスより先に判定する
        if _is_bot_challenge(response):
            self.log.warning(
                "premium login blocked by bot protection: status=%s, cf-mitigated=%s, cf-ray=%s",
                response.status_code,
                response.headers.get("cf-mitigated"),
                response.headers.get("cf-ray"),
            )
            raise PremiumAuthError(PremiumAuthError.BLOCKED, "HTTP %s" % response.status_code)
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

        if not self._store(radiko_session, mail, password, _generation, _logout_old):
            return False
        self.log.info("premium login succeeded (areafree enabled)")
        return True

    def login_with_session(self, radiko_session, assume_areafree=False):
        """ブラウザでログイン済みの radiko_session を検証して使う。失敗時は PremiumAuthError

        assume_areafree が True のとき、検証がボット対策で拒否されたら
        エリアフリーが有効なセッションとみなして受け入れる（期限切れは再生時に検出する）。
        """
        try:
            self.check_session(radiko_session)
        except PremiumAuthError as e:
            if e.kind == PremiumAuthError.NOT_AREAFREE:
                # 使わないセッションは破棄する。ブラウザに残ったままだと次回も同じアカウントになってしまう
                self._post_logout(radiko_session)
            if not (assume_areafree and e.kind == PremiumAuthError.BLOCKED):
                raise
            self.log.warning("premium session check was blocked; accepting the session without verification")
        self._store(radiko_session, None, None)
        self.log.info("premium login with browser session succeeded (areafree enabled)")
        return True

    def check_session(self, radiko_session):
        """radiko_session がログイン中かつエリアフリー有効か確かめる。そうでなければ PremiumAuthError"""
        try:
            response = requests.get(
                LOGIN_CHECK_URL,
                cookies={"radiko_session": radiko_session},
                headers={"User-Agent": constants.BROWSER_USER_AGENT},
                timeout=15,
            )
        except requests.RequestException as e:
            self.log.warning("premium session check request failed: %s", e)
            raise PremiumAuthError(PremiumAuthError.NETWORK, str(e))

        if _is_bot_challenge(response):
            self.log.warning(
                "premium session check blocked by bot protection: status=%s, cf-mitigated=%s, cf-ray=%s",
                response.status_code,
                response.headers.get("cf-mitigated"),
                response.headers.get("cf-ray"),
            )
            raise PremiumAuthError(PremiumAuthError.BLOCKED, "HTTP %s" % response.status_code)
        if response.status_code in (400, 401, 403):
            # 未ログイン・期限切れのセッションには 400 (session error) が返る
            self.log.info("premium session is not logged in: status=%s", response.status_code)
            raise PremiumAuthError(PremiumAuthError.SESSION_EXPIRED)
        if not response.ok:
            self.log.warning("premium session check failed: status=%s", response.status_code)
            raise PremiumAuthError(PremiumAuthError.NETWORK, "HTTP %s" % response.status_code)
        try:
            data = response.json()
        except ValueError:
            self.log.warning("premium session check response is not json")
            raise PremiumAuthError(PremiumAuthError.NETWORK, "invalid response")
        if str(data.get("status")) != "200":
            self.log.info("premium session is not logged in: status=%s", data.get("status"))
            raise PremiumAuthError(PremiumAuthError.SESSION_EXPIRED)
        if not is_areafree_flag(data.get("areafree")):
            self.log.info("premium session is valid but areafree is not enabled")
            raise PremiumAuthError(PremiumAuthError.NOT_AREAFREE)

    def _store(self, radiko_session, mail, password, _generation=None, _logout_old=True):
        """ログイン結果を保持する。途中でログアウトされていたら破棄して False"""
        with self._lock:
            if _generation is not None and _generation != self._generation:
                discarded = True
            else:
                discarded = False
                old_session = self._radiko_session
                self._radiko_session = radiko_session
                self._areafree = True
                self._mail = mail
                self._password = password
        if discarded:
            # 再ログイン中にユーザーがログアウトした。復活させずに破棄する
            self.log.info("premium relogin result discarded (logged out meanwhile)")
            self._post_logout(radiko_session)
            return False
        if _logout_old and old_session and old_session != radiko_session:
            self._post_logout(old_session)
        return True

    def relogin(self):
        """保持している認証情報で再ログインする。成功すれば True

        ブラウザでログインしたセッションは認証情報がないため、再ログインせずにセッションがまだ有効か確かめる。
        期限が切れていれば無効化し、利用者に再度ログインしてもらう。
        """
        with self._lock:
            mail, password = self._mail, self._password
            radiko_session = self._radiko_session
            generation = self._generation
        if (not mail or not password) and not radiko_session:
            return False
        try:
            if not mail or not password:
                self.check_session(radiko_session)
                self.log.info("premium browser session is still valid")
                return True
            # 旧セッションはサーバーへログアウトしない。
            # 再生中・録音中のトークンに影響しないよう、期限切れに任せる
            return self.login(mail, password, _generation=generation, _logout_old=False)
        except PremiumAuthError as e:
            self.log.warning("premium relogin failed: %s", e.kind)
            # 通信障害・ブロックは一時的な可能性があるため、セッションと認証情報を残しておく
            if e.kind not in (PremiumAuthError.NETWORK, PremiumAuthError.BLOCKED):
                self.invalidate(generation, e)
            return False

    def logout(self, keep_browser_session=False):
        """ログアウトする

        keep_browser_session が True のとき、ブラウザでログインしたセッションはサーバーへログアウトしない。
        アプリ終了時に使う（保存したセッションを次回起動時の自動ログインに使うため）。
        """
        with self._lock:
            radiko_session = self._radiko_session
            is_browser_session = not self._mail or not self._password
            self._radiko_session = None
            self._areafree = False
            self._mail = None
            self._password = None
            self._generation += 1
        if not radiko_session:
            return
        if keep_browser_session and is_browser_session:
            self.log.info("premium session released (browser session kept on server)")
            return
        self._post_logout(radiko_session)
        self.log.info("premium logout")

    def invalidate(self, _generation=None, error=None):
        """セッションを無効化する（サーバーへのログアウトは行わない）"""
        with self._lock:
            if _generation is not None and _generation != self._generation:
                return
            was_areafree = bool(self._radiko_session) and self._areafree
            self._radiko_session = None
            self._areafree = False
        if was_areafree:
            self.log.warning("premium session invalidated")
            self._notify_invalidated(error)

    def get_session(self):
        with self._lock:
            return self._radiko_session

    def is_areafree(self):
        with self._lock:
            return bool(self._radiko_session) and self._areafree

    def _post_logout(self, radiko_session):
        try:
            requests.post(
                LOGOUT_URL,
                data={"radiko_session": radiko_session},
                headers={"User-Agent": constants.BROWSER_USER_AGENT},
                timeout=10,
            )
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
    config[CONFIG_SECTION]["method"] = METHOD_PASSWORD
    config[CONFIG_SECTION]["mail"] = mail
    config[CONFIG_SECTION]["password"] = _protect(password)
    config[CONFIG_SECTION]["session"] = ""
    config[CONFIG_SECTION]["autoLogin"] = True


def save_browser_session(config, radiko_session):
    """ブラウザでログインしたセッションを保存する（パスワードは保持しない）"""
    config[CONFIG_SECTION]["method"] = METHOD_BROWSER
    config[CONFIG_SECTION]["password"] = ""
    config[CONFIG_SECTION]["session"] = _protect(radiko_session)
    config[CONFIG_SECTION]["autoLogin"] = True


def clear_credentials(config):
    config[CONFIG_SECTION]["mail"] = ""
    config[CONFIG_SECTION]["password"] = ""
    config[CONFIG_SECTION]["session"] = ""
    config[CONFIG_SECTION]["autoLogin"] = False


def clear_saved_session(config):
    """使えなくなった保存済みセッションを削除する（ログイン方法とメールアドレスは残す）"""
    config[CONFIG_SECTION]["session"] = ""
    config[CONFIG_SECTION]["autoLogin"] = False


def get_saved_method(config):
    """前回のログイン方法を返す"""
    if config[CONFIG_SECTION]["method"] == METHOD_BROWSER:
        return METHOD_BROWSER
    return METHOD_PASSWORD


def _load_protected(config, key):
    encrypted = config[CONFIG_SECTION][key]
    if not encrypted:
        return None
    try:
        return _unprotect(encrypted)
    except Exception as e:
        getLogger("%s.%s" % (constants.LOG_PREFIX, "PremiumSession")).warning(
            "failed to decrypt saved premium %s: %s", key, e
        )
        return None


def load_credentials(config):
    """保存済みの (mail, password) を返す。なければ None"""
    if not config.getboolean(CONFIG_SECTION, "autoLogin", False):
        return None
    if get_saved_method(config) != METHOD_PASSWORD:
        return None
    mail = config[CONFIG_SECTION]["mail"]
    if not mail:
        return None
    password = _load_protected(config, "password")
    if not password:
        return None
    return mail, password


def load_browser_session(config):
    """保存済みのブラウザログインのセッションを返す。なければ None"""
    if not config.getboolean(CONFIG_SECTION, "autoLogin", False):
        return None
    if get_saved_method(config) != METHOD_BROWSER:
        return None
    return _load_protected(config, "session")


def auto_login(config):
    """保存済み認証情報またはセッションで自動ログインする。

    保存されていなければ False。ログインに失敗した場合は PremiumAuthError を送出する
    （ユーザーに知らせないと、保存済みの認証情報で毎回黙って失敗し続けるため）。
    """
    log = getLogger("%s.%s" % (constants.LOG_PREFIX, "PremiumSession"))
    radiko_session = load_browser_session(config)
    if radiko_session:
        try:
            return session.login_with_session(radiko_session, assume_areafree=True)
        except PremiumAuthError as e:
            log.warning("premium auto login with browser session failed: %s", e.kind)
            if e.kind in (PremiumAuthError.SESSION_EXPIRED, PremiumAuthError.NOT_AREAFREE):
                # 使えないセッションで毎回失敗しないよう削除する
                clear_saved_session(config)
                config.write()
            raise

    credentials = load_credentials(config)
    if not credentials:
        return False
    try:
        return session.login(*credentials)
    except PremiumAuthError as e:
        log.warning("premium auto login failed: %s", e.kind)
        raise


def describe_auth_error(error):
    """PremiumAuthError をユーザー向けの説明に変換する"""
    if error.kind == PremiumAuthError.NOT_AREAFREE:
        return _("ログインには成功しましたが、このアカウントではエリアフリーが有効になっていません。\nエリアフリープランの契約状況をご確認ください。")
    if error.kind == PremiumAuthError.INVALID_CREDENTIALS:
        return _("ログインに失敗しました。メールアドレスとパスワードをご確認ください。")
    if error.kind == PremiumAuthError.SESSION_EXPIRED:
        return _("セッションが切れました。再度ログインを行ってください。")
    if error.kind == PremiumAuthError.BLOCKED:
        return _("radikoへのログインが、サーバー側の不正アクセス対策によって拒否されました。\nメールアドレスやパスワードの誤りではありません。\n時間をおいて再度お試しいただくか、ブラウザでのログインをお試しください。")
    return _("radikoへの接続に失敗しました。インターネットの接続状況をご確認ください。")
