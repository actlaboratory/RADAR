# -*- coding: utf-8 -*-
# ラジオ局管理モジュール

import wx
import os
import datetime
import tcutil
import time
import threading
import region_dic
import re
import lxml.etree as ET
import socket
import constants
import globalVars
import urllib
import urllib.error
import requests
from simpleDialog import *
from views.mpvPlayer import MPVAudioPlayer
from views import premium
from views import programmanager


# 聴き逃し再生の終了判定の余裕(秒)と、途中終了時の自動再接続の上限
TIMEFREE_END_MARGIN_SEC = 60
TIMEFREE_RECONNECT_MAX = 3
TIMEFREE_RECONNECT_RESET_SEC = 60
# ライブ再生が途切れたときの自動再接続の間隔(秒)。回数がこれを超えたら最後の値で待つ
LIVE_RECONNECT_INTERVAL_SEC = (5, 10, 20, 30)
# 途切れてからこの秒数つながらなければ再接続をあきらめる（回線の切り替え・ルーターの再起動などを待つ）
LIVE_RECONNECT_GIVE_UP_SEC = 300
# この秒数以上安定して再生できたら、再接続の状態をリセットする
LIVE_RECONNECT_RESET_SEC = 60
# この回数目の再接続から、途切れていることを読み上げる（一瞬の途切れでは読み上げない）
LIVE_RECONNECT_ANNOUNCE_COUNT = 2
# 再生失敗時に原因を調べるため playlist を取得し直すときのタイムアウト(秒)
STREAM_PROBE_TIMEOUT_SEC = 8

NETWORK_ERROR_MESSAGE = "radiko に接続できませんでした。インターネットの接続状況をご確認ください。"


class PlaybackError(RuntimeError):
    """再生を開始できなかったときの例外。メッセージはそのままユーザーに表示できる文言にする"""


def describe_playback_exception(error):
    """再生準備（認証・URL取得）中の例外を、ユーザー向けの説明に変換する"""
    if isinstance(error, PlaybackError):
        return str(error)
    if isinstance(error, urllib.error.HTTPError):
        return f"radiko の認証に失敗しました (HTTP {error.code})。時間をおいて再度お試しください。"
    if isinstance(error, (urllib.error.URLError, requests.RequestException, socket.timeout, ConnectionError)):
        return NETWORK_ERROR_MESSAGE
    return f"再生の準備中にエラーが発生しました: {error}"


def _has_proxy_env():
    """HTTP(S)_PROXY のいずれかがあれば True（直結 TCP 事前検査を省略）。"""
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(k)
        if v is not None and str(v).strip():
            return True
    return False


class RadioManager:
    def __init__(self, parent_view):
        self.parent = parent_view
        self.log = parent_view.log
        self.app = parent_view.app
        self.creator = parent_view.creator
        self.events = parent_view.events
        
        # ラジオ局関連の初期化
        self._player = MPVAudioPlayer()
        self.updateInfoTimer = wx.Timer()
        self.streamWatchdogTimer = wx.Timer()
        self.timefreeSeekTimer = wx.Timer()
        self.timefreeSeekApplyTimer = wx.Timer()
        self.tmg = tcutil.TimeManager()
        self.clutl = tcutil.CalendarUtil()
        self.stid = {}
        self.region = region_dic.REGION
        self.area = None
        # 放送局一覧をエリアフリー（全国）で作成したか
        self.station_list_areafree = False
        self.m3u8 = None
        self.live_http_headers = {}
        self.current_station_id = None
        self.current_progs = None
        self.playback_mode = None
        self._last_timefree_request = None
        self._timefree_started_monotonic = None
        self._timefree_resume_position_sec = 0
        self._timefree_duration_sec = 0
        self._timefree_info = None
        self.stream_watchdog_interval_ms = 5000
        # ライブ再生の復旧処理（再接続・中断理由の判定）を別スレッドで実行中か
        self._live_recovery_running = False
        self._shutting_down = False
        self._timefree_seek_updating_ui = False
        self._pending_seek_seconds = None
        self._last_requested_seek_seconds = None
        self._seek_apply_lock = threading.Lock()
        self._seek_apply_running = False
        self._seek_apply_latest = None
        # シーク(mpv 再起動)と再生開始/停止の競合を防ぐ。世代は再生開始/停止ごとに進める。
        self._playback_lock = threading.RLock()
        self._playback_generation = 0
        # 聴き逃し再生中に mpv が途中終了したときの自動再接続回数
        self._timefree_reconnect_count = 0
        self._timefree_last_reconnect_monotonic = None
        # ライブ再生中に mpv が途中終了したときの自動再接続の状態
        self._live_reconnect_count = 0
        self._live_last_reconnect_monotonic = None
        self._live_outage_started_monotonic = None
        self._live_next_reconnect_monotonic = None
        self._live_outage_announced = False
        self._player.setRestartSourceProvider(self._timefree_restart_source)
        # ライブ再生中に「番組表で過去番組を選択した」ことを示す局 ID（メニュー再開可否用）
        self._timefree_menu_live_ack_station_id = None
        self.streamWatchdogTimer.Bind(wx.EVT_TIMER, self._on_stream_watchdog_timer)
        self.timefreeSeekTimer.Bind(wx.EVT_TIMER, self._on_timefree_seek_timer)
        self.timefreeSeekApplyTimer.Bind(wx.EVT_TIMER, self._on_timefree_seek_apply_timer)

    def setup_radio_ui(self):
        """ラジオ局関連のUIを設定"""
        self.volume, tmp = self.creator.slider(
            _("音量(&V)"), 
            event=self.events.onVolumeChanged, 
            defaultValue=self.app.config.getint("play", "volume", 100, 0, 100), 
            textLayout=None
        )
        self.volume.SetValue(self.app.config.getint("play", "volume"))
        self.timefree_seek_slider, self.timefree_seek_label = self.creator.slider(
            _("聴き逃しシーク"),
            min=0,
            max=1,
            defaultValue=0,
            event=self.onTimefreeSeekChanged,
            x=400,
            sizerFlag=wx.ALL | wx.EXPAND
        )
        # ViewCreatorの既定バインド(EVT_SCROLL_CHANGED)に加え、
        # キー操作中にも即座に反応できるようEVT_SLIDERを追加で受ける。
        self.timefree_seek_slider.Bind(wx.EVT_SLIDER, self.onTimefreeSeekChanged)
        self.timefree_seek_slider.Bind(wx.EVT_KEY_DOWN, self.onTimefreeSeekKeyDown)
        self.timefree_seek_slider.SetPageSize(60)
        self.timefree_seek_label.SetLabel("00:00:00 / 00:00:00")
        self._set_timefree_seek_ui_visible(False)
        
        self.AreaTreeCtrl()
        self.setupradio()
        self.setRadioList()
        self.update_timefree_command_ui()


    def AreaTreeCtrl(self):
        """放送局のツリーコントロールを作成"""
        self.tree, broadcaster = self.creator.treeCtrl(_("放送局"), size=(450,200), proportion=1)

    def setupradio(self):
        """ステーションidを取得後、ツリービューに描画"""
        if self.area in self.region:
            self.log.debug("region:" + self.region[self.area])
        # ツリーのルート項目の作成
        root = self.tree.AddRoot(_("放送局一覧"))
        # エリア情報の取得に失敗
        if not self.area:
            errorDialog(_("エリア情報の取得に失敗しました。\nインターネットの接続状況をご確認ください"))
            self.tree.SetFocus()
            self.tree.Expand(root)
            self.tree.SelectItem(root, select=True)
            return

    def get_radio_stations(self, url, max_retries=3, timeout=30):
        """
        ラジオ局情報を取得する関数
        
        Parameters:
        - url: radiko.jpのAPI URL
        - max_retries: 最大リトライ回数
        - timeout: タイムアウト時間（秒）
        
        Returns:
        - tuple: (成功/失敗, XMLデータ/エラーメッセージ)
        """
        for attempt in range(max_retries):
            try:
                parsed_url = urllib.parse.urlparse(url)
                host = parsed_url.hostname
                port = parsed_url.port or 443

                if not _has_proxy_env():
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(timeout)
                    result = sock.connect_ex((host, port))
                    sock.close()

                    if result != 0:
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 2
                            self.log.debug(
                                f"Connection error; retrying after {wait_time}s "
                                f"(attempt {attempt + 1}/{max_retries})"
                            )
                            time.sleep(wait_time)
                            continue
                        return False, "接続に失敗しました。インターネットの接続状況をご確認ください。"
                else:
                    self.log.debug(
                        "Skipping direct TCP precheck to %s:%s (proxy env present)",
                        host,
                        port,
                    )

                req = urllib.request.Request(url)
                req.add_header('User-Agent', constants.BROWSER_USER_AGENT)
                
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return True, response.read().decode()

            except socket.timeout:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    self.log.debug(f"Timeout; retrying after {wait_time}s")
                    time.sleep(wait_time)
                else:
                    return False, "タイムアウトによりデータの取得に失敗しました。"
                
            except Exception as e:
                self.log.error(f"Unexpected error: {str(e)}")
                return False, f"予期せぬエラーが発生しました: {str(e)}"

    def setRadioList(self):
        """ラジオ局リストを設定"""
        root = self.tree.GetRootItem()
        self.stid = {}
        self.station_list_areafree = False

        # 認証で得たエリアコードの放送局一覧を取得（再生可能局のみ）
        if not self.area:
            errorDialog(_("エリア情報の取得に失敗しました。\nインターネットの接続状況をご確認ください"))
            self.tree.SetFocus()
            self.tree.Expand(root)
            self.tree.SelectItem(root, select=True)
            return

        if premium.session.is_areafree() and self._append_areafree_stations(root):
            self.station_list_areafree = True
            self.tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self.events.onRadioActivated)
            self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.events.onRadioSelected)
            self.tree.SetFocus()
            self.tree.Expand(root)
            self.tree.SelectItem(root, select=True)
            return

        url = f"https://radiko.jp/v2/station/list/{self.area}.xml"
        success, result = self.get_radio_stations(url)
        if not success:
            # v2失敗時のみ従来のv3全体リストをフォールバックとして使用
            self.log.warning(f"Failed to fetch area station list ({self.area}) via v2 API: {result}")
            fallback_url = "https://radiko.jp/v3/station/region/full.xml"
            success, result = self.get_radio_stations(fallback_url)
            if not success:
                errorDialog(_(result))
                self.tree.SetFocus()
                self.tree.Expand(root)
                self.tree.SelectItem(root, select=True)
                return

        try:
            # XMLのパース
            parsed = ET.fromstring(result.encode('utf-8'))

            stations = parsed.findall(".//station")
            if stations:
                # v2/station/list/JPxx.xml 形式
                for station in stations:
                    name_element = station.find("name")
                    id_element = station.find("id")
                    if name_element is None or id_element is None:
                        continue
                    station_name = name_element.text or ""
                    station_id = id_element.text or ""
                    if not station_name or not station_id:
                        continue
                    self.tree.AppendItem(root, station_name, data=station_id)
                    self.stid[station_id] = station_name
            else:
                # v3/station/region/full.xml 形式（フォールバック）
                for r in parsed:
                    for station in r:
                        stream = {r.attrib["ascii_name"]: {}}
                        stream[r.attrib["ascii_name"]] = {
                            "radioname": station.find("name").text,
                            "radioid": station.find("id").text
                        }

                        if "ZENKOKU" in stream:
                            self.tree.AppendItem(root, stream["ZENKOKU"]["radioname"], data=stream["ZENKOKU"]["radioid"])
                            self.stid[stream["ZENKOKU"]["radioid"]] = stream["ZENKOKU"]["radioname"]

                        if self.region[self.area] in stream:
                            self.tree.AppendItem(root, stream[self.region[self.area]]["radioname"], data=stream[self.region[self.area]]["radioid"])
                            self.stid[stream[self.region[self.area]]["radioid"]] = stream[self.region[self.area]]["radioname"]

        except ET.ParseError:
            self.log.error("Failed to parse xml!")
            errorDialog(_("放送局情報の取得に失敗しました。\nしばらく時間をおいて再度お試しください。"))
            return

        except Exception as e:
            self.log.error(f"An unexpected error occurred: {str(e)}")
            return

        # イベントバインドとツリーの設定
        self.tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self.events.onRadioActivated)
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.events.onRadioSelected)
        self.tree.SetFocus()
        self.tree.Expand(root)
        self.tree.SelectItem(root, select=True)

    def _append_areafree_stations(self, root):
        """エリアフリー時、全国の放送局を地方ごとのノードに分けてツリーへ追加する。

        失敗した場合は False を返し、呼び出し側で通常のエリア内一覧にフォールバックする。
        """
        success, result = self.get_radio_stations("https://radiko.jp/v3/station/region/full.xml")
        if not success:
            self.log.warning(f"Failed to fetch full station list for areafree: {result}")
            return False
        try:
            parsed = ET.fromstring(result.encode('utf-8'))
        except ET.ParseError:
            self.log.error("Failed to parse full station list xml for areafree")
            return False

        # エリアフリー非対応（<areafree>0</areafree>。NHK の地域局など）の局は、エリア内で聴ける局だけ載せる
        local_ids = programmanager.get_area_station_ids(self.area)
        stid = {}
        own_region_item = None
        for region in parsed:
            region_label = region.get("region_name") or region.get("ascii_name") or ""
            is_zenkoku = region.get("ascii_name") == "ZENKOKU"
            region_item = None
            for station in region.findall("station"):
                name_element = station.find("name")
                id_element = station.find("id")
                if name_element is None or id_element is None:
                    continue
                station_name = name_element.text or ""
                station_id = id_element.text or ""
                if not station_name or not station_id or station_id in stid:
                    continue
                area_element = station.find("area_id")
                areafree_element = station.find("areafree")
                if areafree_element is not None and (areafree_element.text or "").strip() == "0":
                    if local_ids is not None:
                        listenable = station_id in local_ids
                    else:
                        listenable = area_element is not None and area_element.text == self.area
                    if not listenable:
                        continue
                if region_item is None:
                    region_item = self.tree.AppendItem(root, region_label)
                self.tree.AppendItem(region_item, station_name, data=station_id)
                stid[station_id] = station_name
                if own_region_item is None and not is_zenkoku and area_element is not None and area_element.text == self.area:
                    own_region_item = region_item

        if not stid:
            self.log.warning("Full station list for areafree is empty")
            self.tree.DeleteChildren(root)
            return False
        # 番組キャッシュ（番組検索）も self.stid を参照するため、全国の局が収集対象になる
        self.stid = stid
        if own_region_item is not None:
            self.tree.Expand(own_region_item)
        self.log.info(f"areafree station list: {len(stid)} stations")
        return True

    def areaDetermination(self, progs):
        """エリアを判定する"""
        self.area = progs.getArea()

    def _start_live_recovery_thread(self, target, args, name):
        """ライブ再生の復旧処理（再接続・中断理由の判定）を別スレッドで開始する。

        認証や配信サーバーへの通信で UI スレッドを止めないため、通信はすべてこのスレッドで行う。
        同時に実行するのは1つだけ。
        """
        if self._live_recovery_running:
            self.log.debug(f"live recovery already running; skip {name}")
            return
        self._live_recovery_running = True

        def _run():
            try:
                target(*args)
            except Exception:
                self.log.exception(f"unexpected error in {name}")
            finally:
                self._live_recovery_running = False

        try:
            threading.Thread(target=_run, name=name, daemon=True).start()
        except Exception:
            self._live_recovery_running = False
            self.log.exception(f"failed to start {name} thread")

    def _live_reconnect_worker(self, generation, station_id, progs, max_attempts=2):
        """（別スレッド）ストリームURLを取得し直して mpv を起動し直す。

        途中で停止・別の局への切替があった場合（世代が変わった場合）は何もせずに終わる。
        失敗しても次回の watchdog で再試行（または中断）されるため、ここでは例外を送出しない。
        """
        relogged_in = False
        for attempt in range(1, max_attempts + 1):
            if self._shutting_down or generation != self._playback_generation:
                self.log.info(f"live reconnect cancelled (playback changed): station={station_id}")
                return
            try:
                stream_url, headers = progs.get_authenticated_stream_url(station_id)
                if not stream_url:
                    raise RuntimeError("empty stream url")
            except Exception as e:
                self.log.warning(
                    f"live reconnect: failed to get stream url ({attempt}/{max_attempts}): "
                    f"station={station_id}: {e!r}"
                )
                time.sleep(0.5)
                continue
            # 停止・切替との競合を防ぐため、世代の確認と mpv の起動はロック内で行う
            with self._playback_lock:
                if self._shutting_down or generation != self._playback_generation:
                    self.log.info(f"live reconnect discarded (playback changed): station={station_id}")
                    return
                self.m3u8 = stream_url
                self.live_http_headers = headers
                self._player.setHttpHeaders(headers)
                self._player.setSource(stream_url)
                started = self._player.play()
            if started:
                self.log.info(f"live reconnect succeeded: station={station_id} (attempt {attempt}/{max_attempts})")
                wx.CallAfter(self._on_live_reconnected, generation)
                return
            last_error = self._player.getLastError() or ""
            self.log.warning(
                f"live reconnect: player failed to start ({attempt}/{max_attempts}): station={station_id}, "
                f"player error={(last_error or 'unknown')[:300]}"
            )
            status = self._probe_stream_status(stream_url, headers)
            if status == 403 and not relogged_in and self._relogin_for_forbidden_stream(station_id, progs):
                # 次の試行で新しいセッションの URL を取得し直す
                relogged_in = True
                continue
            if status in (403, 404):
                # 認証し直しても聴取できない（エリア外・配信なし）。待っても直らないため、すぐに中断する
                reason = self._describe_stream_status(status)
                wx.CallAfter(self._finish_live_give_up, generation, station_id, reason, last_error)
                return
            time.sleep(0.5)
        self.log.error(f"live reconnect failed after {max_attempts} attempts: station={station_id}")

    def _reset_live_reconnect_state(self):
        self._live_reconnect_count = 0
        self._live_last_reconnect_monotonic = None
        self._live_outage_started_monotonic = None
        self._live_next_reconnect_monotonic = None
        self._live_outage_announced = False

    def _on_live_reconnected(self, generation):
        """（UI スレッド）再接続できたとき、途切れたことを読み上げていれば復帰も知らせる"""
        if self._shutting_down or generation != self._playback_generation:
            return
        if not self._live_outage_announced:
            return
        self._live_outage_announced = False
        try:
            self.parent.app.say("再接続しました。", interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce live reconnected: {e}")

    def _live_give_up_worker(self, generation, station_id, stream_url, headers, last_error):
        """（別スレッド）中断理由を判定し、UI スレッドで停止・通知する"""
        reason = self._diagnose_stream_failure(stream_url, headers)
        wx.CallAfter(self._finish_live_give_up, generation, station_id, reason, last_error)

    def _finish_live_give_up(self, generation, station_id, reason, last_error):
        """（UI スレッド）再接続を諦めて停止し、理由を読み上げる"""
        if self._shutting_down or generation != self._playback_generation:
            self.log.info(f"live give-up discarded (playback changed): station={station_id}")
            return
        self.log.error(
            f"live playback gave up after {self._live_reconnect_count} reconnects: station={station_id}, "
            f"reason={reason}, last player error={last_error[:300] or '(none)'}"
        )
        self._reset_playback_state()
        try:
            self.parent.app.say(f"再生が中断されました。{reason}", interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce playback interruption: {e}")

    def _on_stream_watchdog_timer(self, event):
        """再生停止を検知して再認証・再接続する（通信は別スレッドで行う）"""
        try:
            self._check_live_stream()
        except Exception:
            self.log.exception("stream watchdog failed")

    def _check_live_stream(self):
        if self._shutting_down:
            return
        if not self.events.playing:
            return
        if self.playback_mode != "live":
            return
        if self._live_recovery_running:
            return
        now = time.monotonic()
        last = self._live_last_reconnect_monotonic
        if self._player.isPlaying():
            # 一定時間安定して再生できていれば、再接続の状態をリセットする
            if last is not None and now - last > LIVE_RECONNECT_RESET_SEC:
                self.log.info("live playback stable; reset reconnect state")
                self._reset_live_reconnect_state()
            return
        last_error = self._player.getLastError() or ""
        return_code = self._player.getReturnCode()
        generation = self._playback_generation
        station_id = self.current_station_id
        if self._live_outage_started_monotonic is None:
            self._live_outage_started_monotonic = now
        if now - self._live_outage_started_monotonic >= LIVE_RECONNECT_GIVE_UP_SEC:
            self.log.warning(
                f"live playback could not be restored for {LIVE_RECONNECT_GIVE_UP_SEC}s "
                f"({self._live_reconnect_count} reconnects); diagnosing: station={station_id}"
            )
            self._start_live_recovery_thread(
                self._live_give_up_worker,
                (generation, station_id, self.m3u8, dict(self.live_http_headers or {}), last_error),
                "LiveGiveUp",
            )
            return
        if self._live_next_reconnect_monotonic is not None and now < self._live_next_reconnect_monotonic:
            return
        if not station_id or not self.current_progs:
            # 再接続できない不整合な状態。watchdog が同じログを出し続けないよう停止する
            self.log.error("live playback stopped but station info is missing; cannot reconnect. stopping")
            self._reset_playback_state()
            try:
                self.parent.app.say("再生が中断されました。", interrupt=True)
            except Exception as e:
                self.log.error(f"Failed to announce playback interruption: {e}")
            return
        self._live_reconnect_count += 1
        self._live_last_reconnect_monotonic = now
        interval = LIVE_RECONNECT_INTERVAL_SEC[min(self._live_reconnect_count, len(LIVE_RECONNECT_INTERVAL_SEC)) - 1]
        self._live_next_reconnect_monotonic = now + interval
        self.log.warning(
            f"live playback stopped unexpectedly (returncode={return_code}); reconnecting "
            f"(attempt {self._live_reconnect_count}, next in {interval}s): station={station_id}, "
            f"last player error={last_error[:300] or '(none)'}"
        )
        if self._live_reconnect_count == LIVE_RECONNECT_ANNOUNCE_COUNT:
            self._live_outage_announced = True
            try:
                self.parent.app.say("接続が途切れました。再接続しています。", interrupt=True)
            except Exception as e:
                self.log.error(f"Failed to announce live reconnecting: {e}")
        self._start_live_recovery_thread(
            self._live_reconnect_worker,
            (generation, station_id, self.current_progs),
            "LiveReconnect",
        )

    def _probe_stream_status(self, stream_url, headers):
        """playlist を取得し直して HTTP ステータスを返す。URL がない・通信できない場合は None"""
        if not stream_url:
            return None
        try:
            response = requests.get(
                stream_url,
                headers={"User-Agent": constants.BROWSER_USER_AGENT, **(headers or {})},
                timeout=STREAM_PROBE_TIMEOUT_SEC,
            )
        except requests.RequestException as e:
            self.log.warning(f"stream probe failed: {e}")
            return None
        self.log.info(f"stream probe: status={response.status_code}")
        return response.status_code

    def _relogin_for_forbidden_stream(self, station_id, progs):
        """エリアフリーで再生している局が 403 のとき、プレミアムのセッション切れとみて再ログインする。

        auth2 はセッションが切れていてもエラーにならない場合があるため、配信側の 403 を契機にする。
        再ログインできたら True（呼び出し側で URL を取得し直して再試行する）。
        """
        try:
            if not progs or not progs.uses_areafree(station_id):
                return False
        except Exception as e:
            self.log.warning(f"failed to check areafree for {station_id}: {e!r}")
            return False
        self.log.warning(f"areafree stream is forbidden (403); trying premium relogin: station={station_id}")
        return premium.session.relogin()

    def _diagnose_stream_failure(self, stream_url, headers):
        """mpv が再生できなかった原因を、playlist を取得し直して推定しユーザー向けの説明を返す"""
        if not stream_url:
            return "再生 URL を取得できませんでした。"
        return self._describe_stream_status(self._probe_stream_status(stream_url, headers))

    def _describe_stream_status(self, status):
        """_probe_stream_status の結果から、再生できなかった原因のユーザー向けの説明を返す"""
        if status is None:
            return NETWORK_ERROR_MESSAGE
        if status == 403:
            if premium.session.is_areafree():
                return "この放送局を再生する権限がありません。radikoプレミアムのログイン状態をご確認ください。"
            return (
                "この放送局は現在の地域では聴取できません。"
                "エリア外の放送局を聴くには、radikoプレミアム（エリアフリー）へのログインが必要です。"
            )
        if status == 401:
            return "radiko の認証の有効期限が切れました。もう一度再生してください。"
        if status == 404:
            return "配信が見つかりませんでした。放送局一覧を再描画してからお試しください。"
        if status >= 400:
            return f"配信サーバーがエラーを返しました (HTTP {status})。時間をおいて再度お試しください。"
        # 配信は取得できるのにプレイヤーが終了した場合は、出力デバイスなど再生側の問題
        last_error = (self._player.getLastError() or "").strip()
        detail = f"（{last_error[:200]}）" if last_error else ""
        return f"プレイヤーで再生できませんでした。再生出力デバイスの設定をご確認ください。{detail}"

    def _reset_playback_state(self):
        """再生失敗・中断時に、停止状態へ戻す（読み上げはしない）"""
        self._begin_playback_change()
        try:
            self._player.stop()
        except Exception as e:
            self.log.warning(f"Failed to stop player while resetting playback state: {e}")
        self.updateInfoTimer.Stop()
        self.streamWatchdogTimer.Stop()
        self.current_station_id = None
        self.events.current_playing_station_id = None
        self.current_progs = None
        self.playback_mode = None
        self._timefree_started_monotonic = None
        self.events.playing = False
        self._reset_live_reconnect_state()
        try:
            self._stop_timefree_seek_timer()
            self._set_timefree_seek_ui_visible(False)
            self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("再生"))
            self.update_timefree_command_ui()
            if hasattr(self.parent, "program_info_handler"):
                self.parent.program_info_handler.clear_timefree_program_info()
        except Exception as e:
            self.log.error(f"Failed to reset playback UI: {e}")

    def player(self):
        """再生用関数。mpv が起動できたら True"""
        self._player.setSource(self.m3u8)
        self._player.setVolume(self.volume.GetValue())
        self.log.info("playing...")
        return self._player.play()

    def play(self, id, progs, program_info=None):
        """ライブ再生を開始する。

        認証・mpv の起動は別スレッドで行い、すぐに戻る（通信が遅くても画面を止めない）。
        開始できなかった場合は停止状態に戻し、理由をエラーダイアログで知らせる。
        """
        if self._shutting_down:
            self.log.info(f"live playback request ignored during shutdown: station={id}")
            return
        self.log.info(f"live playback requested: station={id}, areafree={premium.session.is_areafree()}")
        self._begin_playback_change()
        generation = self._playback_generation
        if self._player.isPlaying():
            try:
                self._player.stop()
            except Exception as e:
                self.log.warning(f"Failed to stop previous playback: {e}")
        self.clear_timefree_menu_live_ack()
        self.streamWatchdogTimer.Stop()
        volume = self.volume.GetValue()
        threading.Thread(
            target=self._live_start_worker,
            args=(generation, id, progs, program_info, volume),
            name="LiveStart",
            daemon=True,
        ).start()

    def _start_live_player(self, generation, stream_url, headers, volume):
        """（別スレッド）mpv を起動する。起動したら True、停止・切替済みなら None"""
        # 停止・切替との競合を防ぐため、世代の確認と mpv の起動はロック内で行う
        with self._playback_lock:
            if self._shutting_down or generation != self._playback_generation:
                return None
            self.m3u8 = stream_url
            self.live_http_headers = headers
            self._player.setNonSeekableInput(True)
            self._player.setHttpHeaders(headers)
            self._player.setStartPosition(0)
            self._player.setSource(stream_url)
            self._player.setVolume(volume)
            self.log.info("playing...")
            return self._player.play()

    def _live_start_worker(self, generation, id, progs, program_info, volume):
        """（別スレッド）認証して mpv を起動し、結果を UI スレッドへ渡す"""
        try:
            stream_url, headers = progs.get_authenticated_stream_url(id)
            if not stream_url:
                raise RuntimeError("empty stream url")
        except Exception as e:
            self.log.error(f"Failed to get stream url: station={id}: {e!r}")
            wx.CallAfter(self._finish_live_start, generation, id, progs, program_info, False, describe_playback_exception(e))
            return
        started = self._start_live_player(generation, stream_url, headers, volume)
        if started is None:
            self.log.info(f"live playback start discarded (playback changed): station={id}")
            return
        status = None
        if not started:
            status = self._probe_stream_status(stream_url, headers)
            if status == 403 and self._relogin_for_forbidden_stream(id, progs):
                try:
                    stream_url, headers = progs.get_authenticated_stream_url(id)
                    if stream_url:
                        started = self._start_live_player(generation, stream_url, headers, volume)
                        if started is None:
                            return
                except Exception as e:
                    self.log.warning(f"Failed to retry live playback after relogin: station={id}: {e!r}")
                if not started:
                    status = self._probe_stream_status(stream_url, headers)
        reason = None
        if not started:
            reason = self._describe_stream_status(status) if stream_url else "再生 URL を取得できませんでした。"
            self.log.error(
                f"Failed to start live playback: station={id}, reason={reason}, "
                f"player error={(self._player.getLastError() or '')[:300]}"
            )
        wx.CallAfter(self._finish_live_start, generation, id, progs, program_info, started, reason)

    def _finish_live_start(self, generation, id, progs, program_info, started, reason):
        """（UI スレッド）再生開始の結果を画面と状態に反映する"""
        if self._shutting_down or generation != self._playback_generation:
            # 起動中に停止・別の局への切替があった（mpv は停止・切替側で止めている）
            self.log.info(f"live playback start result discarded (playback changed): station={id}")
            return
        if not started:
            self._reset_playback_state()
            errorDialog(_("再生に失敗しました。\n%(detail)s") % {"detail": reason})
            return

        self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("停止"))
        self.current_station_id = id
        self.current_progs = progs
        self.events.current_playing_station_id = id
        self.playback_mode = "live"
        self._timefree_started_monotonic = None
        self._timefree_resume_position_sec = 0
        self._timefree_duration_sec = 0
        self._timefree_info = None
        self._reset_live_reconnect_state()
        self.events.playing = True
        self.streamWatchdogTimer.Start(self.stream_watchdog_interval_ms)
        self.log.info(f"live playback started: station={id}")
        # 以降は表示まわりの更新のみ。失敗しても再生は継続させる
        try:
            self.update_program_info()
            self._stop_timefree_seek_timer()
            self._set_timefree_seek_ui_visible(False)
            self.update_timefree_command_ui()
            if hasattr(self.parent, "program_info_handler"):
                handler = self.parent.program_info_handler
                handler.clear_timefree_program_info()
                if self.events.displaying:
                    if program_info:
                        handler.show_program_info_snapshot(program_info)
                    else:
                        handler.get_latest_info()
        except Exception as e:
            self.log.error(f"Failed to update UI after playback start: {e}")

        try:
            station_name = self.stid.get(id, id)
            self.parent.app.say(f"再生開始: {station_name}", interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce playback start: {e}")

    def play_timefree(self, stream_url, station_id=None, announce_text=None, headers=None, resume_seconds=0, timefree_info=None):
        """タイムフリーURLで再生開始"""
        if self._shutting_down:
            self.log.info(f"timefree playback request ignored during shutdown: station={station_id}")
            return
        self.log.info(
            f"timefree playback requested: station={station_id}, resume={resume_seconds}s, "
            f"areafree={premium.session.is_areafree()}"
        )
        self._begin_playback_change()
        if self._player.isPlaying():
            try:
                self._player.stop()
            except Exception as e:
                self.log.warning(f"Failed to stop previous playback: {e}")
        self.updateInfoTimer.Stop()
        self.streamWatchdogTimer.Stop()
        self.log.debug(f"Start timefree playback: station={station_id}, url={stream_url}")
        self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("再生"))
        self.current_station_id = station_id
        self.events.current_playing_station_id = station_id
        self.current_progs = None
        self.playback_mode = "timefree"
        self.m3u8 = stream_url
        resume_seconds = max(0, int(resume_seconds or 0))
        self._timefree_reconnect_count = 0
        self._timefree_last_reconnect_monotonic = None
        self._timefree_resume_position_sec = resume_seconds
        self._timefree_started_monotonic = time.monotonic()
        self._timefree_info = dict(timefree_info or {})
        duration = self._timefree_info.get("duration_sec", 0) if self._timefree_info else 0
        self._timefree_duration_sec = max(0, int(duration or 0))
        self._last_timefree_request = {
            "stream_url": stream_url,
            "station_id": station_id,
            "announce_text": announce_text,
            "headers": headers or {},
            "resume_seconds": resume_seconds,
            "timefree_info": dict(timefree_info or {}),
            "ft_dt": (timefree_info or {}).get("ft_dt"),
            "to_dt": (timefree_info or {}).get("to_dt"),
            "stream_type": (timefree_info or {}).get("stream_type", "b"),
        }
        self._player.setNonSeekableInput(True)
        self._player.setHttpHeaders(headers or {})
        self._player.setStartPosition(0)
        started = self.player()
        if started:
            time.sleep(0.9)
        if not started or not self._player.isPlaying():
            reason = self._diagnose_stream_failure(stream_url, headers)
            self.log.error(
                f"Failed to start timefree playback: station={station_id}, reason={reason}, "
                f"player error={(self._player.getLastError() or '')[:300]}"
            )
            # 再開位置などの再生情報(_last_timefree_request)は残し、メニューから再試行できるようにする
            self._reset_playback_state()
            raise PlaybackError(reason)
        self.events.playing = False
        self.log.info(f"timefree playback started: station={station_id}, position={resume_seconds}s")
        # 以降は表示まわりの更新のみ。失敗しても再生は継続させる
        try:
            self._set_timefree_seek_ui_visible(True)
            self._sync_timefree_seek_ui_from_player()
            self._start_timefree_seek_timer()
            self.update_timefree_command_ui()
            self.update_program_info()
            if hasattr(self.parent, "program_info_handler"):
                self.parent.program_info_handler.show_timefree_program_info(self._timefree_info)
        except Exception as e:
            self.log.error(f"Failed to update UI after timefree playback start: {e}")
        try:
            if announce_text:
                self.parent.app.say(announce_text, interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce timefree playback start: {e}")

    def is_timefree_playing(self):
        """聴き逃し再生中かどうか"""
        return self.playback_mode == "timefree"

    def is_playing_timefree_program(self, station_id, start_dt, end_dt):
        """指定番組が現在の聴き逃し再生対象かどうか"""
        if self.playback_mode != "timefree":
            return False
        info = self._timefree_info or {}
        if info.get("station_id") != station_id:
            return False
        playing_start = info.get("ft_dt")
        playing_end = info.get("to_dt")
        if playing_start is not None and playing_end is not None:
            return start_dt == playing_start and end_dt == playing_end
        try:
            playing_start = datetime.datetime.strptime(
                info.get("start_time", ""), "%Y-%m-%d %H:%M:%S"
            )
            playing_end = datetime.datetime.strptime(
                info.get("end_time", ""), "%Y-%m-%d %H:%M:%S"
            )
            return start_dt == playing_start and end_dt == playing_end
        except (TypeError, ValueError):
            return False

    def has_last_timefree_request(self):
        """再開可能な聴き逃し再生情報があるか"""
        return self._last_timefree_request is not None

    def replay_last_timefree(self):
        """最後の聴き逃し再生を再開"""
        if not self._last_timefree_request:
            raise RuntimeError("再開可能な聴き逃し再生情報がありません。")
        target = int(self._last_timefree_request.get("resume_seconds", 0) or 0)
        self._replay_timefree_at(target, announce=True)

    def get_timefree_position_seconds(self):
        """現在の聴き逃し再生位置(秒)"""
        if self.playback_mode != "timefree":
            return int(max(0, self._timefree_resume_position_sec))
        elapsed = 0
        if self._timefree_started_monotonic:
            elapsed = max(0, int(time.monotonic() - self._timefree_started_monotonic))
        pos = int(max(0, self._timefree_resume_position_sec + elapsed))
        if self._timefree_duration_sec > 0:
            pos = min(pos, self._timefree_duration_sec)
        return pos

    def get_timefree_duration_seconds(self):
        """現在の聴き逃し番組の尺(秒)"""
        return int(max(0, self._timefree_duration_sec))

    def _get_program_manager(self):
        """共有の ProgramManager を返す（生成時の放送局一覧取得を毎回行わないため）"""
        return getattr(self.parent, "progs", None) or programmanager.ProgramManager()

    def _begin_playback_change(self):
        """再生開始/停止の前に呼び、実行中のシーク処理を無効化する（シーク中の再起動完了を待つ）"""
        with self._playback_lock:
            self._playback_generation += 1

    def seek_timefree(self, seconds, generation=None, refresh_auth=False):
        """聴き逃し再生位置を移動

        radiko のタイムフリー playlist は l=15 秒ずつ返るライブ形式のため、
        mpv 内でのシークは効かない。seek 指定で URL を作り直して mpv を再起動する。
        generation はシーク要求時点の再生世代。停止・別番組への切替後は何もしない。
        """
        if generation is None:
            generation = self._playback_generation
        req = self._last_timefree_request
        if not req:
            raise RuntimeError("シーク可能な聴き逃し再生情報がありません。")
        ft_dt = req.get("ft_dt")
        to_dt = req.get("to_dt")
        station_id = req.get("station_id")
        stream_type = req.get("stream_type", "b")
        if not (ft_dt and to_dt and station_id):
            raise RuntimeError("シーク可能な聴き逃し再生情報がありません。")
        target = max(0, int(seconds or 0))
        duration = self.get_timefree_duration_seconds()
        if duration > 0:
            target = min(target, duration)

        pm = self._get_program_manager()
        if refresh_auth:
            stream_url, headers = pm.get_timefree_playback_source_with_seek(
                station_id=station_id,
                ft_dt=ft_dt,
                to_dt=to_dt,
                seek_seconds=target,
                stream_type=stream_type,
            )
        else:
            # 認証(auth1/auth2)は時間がかかるため、再生中のトークンを使い回す。
            # 期限切れなら mpv が終了し、_handle_timefree_player_exit が認証し直して再接続する。
            headers = req.get("headers") or {}
            stream_url = pm.build_timefree_seek_url(station_id, ft_dt, to_dt, target, stream_type)
        result = self._restart_timefree_player_at(generation, req, stream_url, headers, target)
        if result is False:
            raise RuntimeError(
                f"タイムフリーシークに失敗しました: {self._player.getLastError() or 'unknown'}"
            )

    def _restart_timefree_player_at(self, generation, req, stream_url, headers, target):
        """シーク先 URL で mpv を起動し直す。

        戻り値: 起動成功 True / 失敗 False / 停止・切替済みで何もしなかった None
        """
        with self._playback_lock:
            if generation != self._playback_generation or not self.is_timefree_playing():
                return None
            self._player.setNonSeekableInput(True)
            self._player.setStartPosition(0)
            if not self._player.restartWithSource(stream_url, headers):
                return False
            req["stream_url"] = stream_url
            req["headers"] = headers
            req["resume_seconds"] = target
            self.m3u8 = stream_url
            self._timefree_resume_position_sec = target
            self._timefree_started_monotonic = time.monotonic()
            return True

    def _timefree_restart_source(self):
        """音量/デバイス変更で mpv が再起動されるとき、現在位置から再開する (URL, ヘッダー) を返す。

        聴き逃し再生中以外は None を返し、元のソースのまま再起動させる。
        mpv のロック内から呼ばれるため、ここでは _playback_lock を取らない（取得順の逆転を避ける）。
        """
        req = self._last_timefree_request
        if not req or not self.is_timefree_playing():
            return None
        ft_dt = req.get("ft_dt")
        to_dt = req.get("to_dt")
        station_id = req.get("station_id")
        if not (ft_dt and to_dt and station_id):
            return None
        target = self.get_timefree_position_seconds()
        stream_url = self._get_program_manager().build_timefree_seek_url(
            station_id, ft_dt, to_dt, target, req.get("stream_type", "b")
        )
        headers = req.get("headers") or {}
        req["stream_url"] = stream_url
        req["resume_seconds"] = target
        self.m3u8 = stream_url
        self._timefree_resume_position_sec = target
        self._timefree_started_monotonic = time.monotonic()
        return stream_url, headers

    def _replay_timefree_at(self, target_seconds, announce=False):
        """seek秒位置でURLを再生成してタイムフリー再生"""
        req = self._last_timefree_request or {}
        ft_dt = req.get("ft_dt")
        to_dt = req.get("to_dt")
        station_id = req.get("station_id")
        stream_type = req.get("stream_type", "b")

        if ft_dt and to_dt and station_id:
            pm = self._get_program_manager()
            stream_url, headers = pm.get_timefree_playback_source_with_seek(
                station_id=station_id,
                ft_dt=ft_dt,
                to_dt=to_dt,
                seek_seconds=target_seconds,
                stream_type=stream_type,
            )
            info = dict(req.get("timefree_info") or {})
            info["stream_type"] = stream_type
            self.play_timefree(
                stream_url,
                station_id=station_id,
                announce_text=req.get("announce_text") if announce else None,
                headers=headers,
                resume_seconds=target_seconds,
                timefree_info=info,
            )
            return

        self.play_timefree(
            req.get("stream_url"),
            station_id=station_id,
            announce_text=req.get("announce_text") if announce else None,
            headers=req.get("headers"),
            resume_seconds=target_seconds,
            timefree_info=req.get("timefree_info"),
        )

    def stop_timefree(self, announce_text="聴き逃し再生停止", resume_seconds=None):
        """聴き逃し再生を停止（resume_seconds 指定時は次回再開位置をその値にする）"""
        self._begin_playback_change()
        if resume_seconds is None:
            current_pos = self.get_timefree_position_seconds()
        else:
            current_pos = max(0, int(resume_seconds))
        if self._last_timefree_request is not None:
            self._last_timefree_request["resume_seconds"] = current_pos
        self._timefree_resume_position_sec = current_pos
        self._timefree_started_monotonic = None
        self.log.info(
            f"timefree playback stopped: station={self.current_station_id}, resume={current_pos}s, reason={announce_text}"
        )
        self._player.stop()
        self.updateInfoTimer.Stop()
        self.streamWatchdogTimer.Stop()
        self.current_station_id = None
        self.events.current_playing_station_id = None
        self.current_progs = None
        self.playback_mode = None
        self.events.playing = False
        self._stop_timefree_seek_timer()
        self._set_timefree_seek_ui_visible(False)
        self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("再生"))
        self.update_timefree_command_ui()
        if hasattr(self.parent, "program_info_handler"):
            self.parent.program_info_handler.clear_timefree_program_info()
        try:
            self.parent.app.say(announce_text, interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce timefree playback stop: {e}")

    def stop(self):
        """再生停止"""
        self._begin_playback_change()
        self.clear_timefree_menu_live_ack()
        self._player.stop()
        self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("再生"))
        self.log.info(f"playback stopped by user: station={self.current_station_id}, mode={self.playback_mode}")
        self.updateInfoTimer.Stop()
        self.streamWatchdogTimer.Stop()
        self.current_station_id = None
        self.current_progs = None
        self.playback_mode = None
        self._timefree_started_monotonic = None
        self.log.debug("timer is stoped!")
        self.events.playing = False
        self._stop_timefree_seek_timer()
        self._set_timefree_seek_ui_visible(False)
        self.update_timefree_command_ui()
        if hasattr(self.parent, "program_info_handler"):
            self.parent.program_info_handler.clear_timefree_program_info()

        try:
            self.parent.app.say("再生停止", interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce playback stop: {e}")

    def update_program_info(self):
        """番組情報更新タイマーを開始"""
        self.updateInfoTimer.Start(self.tmg.replace_milliseconds(3))
        self.updateInfoTimer.Bind(wx.EVT_TIMER, self.events.onUpdateProcess)

    def get_latest_programList(self, progs):
        """番組リストを最新に更新"""
        # 認証（エリア判定）を先に行い、失敗したら今の一覧を残す（消してから失敗すると一覧が空になる）
        try:
            self.areaDetermination(progs)
        except Exception as e:
            self.log.error(f"Failed to determine area while reloading station list: {e!r}")
            errorDialog(_("エリア情報の取得に失敗しました。\nインターネットの接続状況をご確認ください"))
            return
        was_displaying = self.events.displaying
        self.tree.DeleteAllItems()
        # 放送局リスト更新時は番組情報UIを一旦破棄し、メニューを一時的に無効にする。
        self.parent.program_info_handler.destroy_program_info_ui(disable_menu=True)
        self.creator.GetSizer().Layout()
        self.setupradio()
        self.setRadioList()
        self._sync_program_info_menu_after_station_list_reload(was_displaying)
        self.creator.GetSizer().Layout()

    def _sync_program_info_menu_after_station_list_reload(self, was_displaying):
        """放送局一覧の再構築後、番組情報の表示設定とメニューを整合させる"""
        handler = self.parent.program_info_handler
        menu = self.parent.menu
        if was_displaying:
            menu.SetMenuLabel("HIDE_PROGRAMINFO", _("番組情報の非表示(&H)"))
            handler.ensure_program_info_ui()
            handler._set_program_info_menu_enabled(True)
            handler.get_latest_info()
        else:
            menu.SetMenuLabel("HIDE_PROGRAMINFO", _("番組情報を表示(&P)"))
            handler._set_program_info_menu_enabled(True)

    def exit(self):
        """終了処理"""
        if self._shutting_down:
            return
        self.log.info("RadioManager.exit: stopping timers and shutting down mpv")
        self._shutting_down = True
        self._begin_playback_change()
        self.events.playing = False
        self.playback_mode = None
        self.current_station_id = None
        self.current_progs = None
        self.streamWatchdogTimer.Stop()
        self.updateInfoTimer.Stop()
        self._stop_timefree_seek_timer()
        self._player.exit()
        self.log.info(
            "RadioManager.exit: mpv shutdown complete "
            "(timefree seek daemon thread interrupted by mpv stop)"
        )

    def _format_hhmmss(self, seconds):
        total = int(max(0, seconds))
        hh = total // 3600
        mm = (total % 3600) // 60
        ss = total % 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}"

    def _set_timefree_seek_ui_visible(self, visible):
        if not hasattr(self, "timefree_seek_slider"):
            return
        self.timefree_seek_slider.Show(visible)
        self.timefree_seek_label.Show(visible)
        self.timefree_seek_slider.Enable(visible)
        try:
            self.parent.hPanel.Layout()
            self.parent.sizer.Layout()
        except Exception:
            pass

    def _sync_timefree_seek_ui_from_player(self):
        if not hasattr(self, "timefree_seek_slider"):
            return
        if not self.is_timefree_playing():
            return
        duration = self.get_timefree_duration_seconds()
        position = self.get_timefree_position_seconds()
        if duration <= 0:
            duration = max(position, 1)
        position = max(0, min(position, duration))
        self._timefree_seek_updating_ui = True
        try:
            self.timefree_seek_slider.SetRange(0, int(duration))
            self.timefree_seek_slider.SetValue(int(position))
            self.timefree_seek_label.SetLabel(
                f"{self._format_hhmmss(position)} / {self._format_hhmmss(duration)}"
            )
        finally:
            self._timefree_seek_updating_ui = False

    def _start_timefree_seek_timer(self):
        if not self.timefreeSeekTimer.IsRunning():
            self.timefreeSeekTimer.Start(1000)

    def _stop_timefree_seek_timer(self):
        if self.timefreeSeekTimer.IsRunning():
            self.timefreeSeekTimer.Stop()
        if self.timefreeSeekApplyTimer.IsRunning():
            self.timefreeSeekApplyTimer.Stop()

    def _on_timefree_seek_timer(self, event):
        if self.is_timefree_playing():
            # 操作中・シーク反映中はスライダーを旧位置へ戻さない
            if (
                self._pending_seek_seconds is not None
                or self._seek_apply_running
                or self.timefreeSeekApplyTimer.IsRunning()
            ):
                return
            if not self._player.isPlaying():
                self._handle_timefree_player_exit()
                return
            self._sync_timefree_seek_ui_from_player()
        else:
            self._stop_timefree_seek_timer()

    def _handle_timefree_player_exit(self):
        """聴き逃し再生中に mpv が終了したとき、番組終了なら停止し、途中なら現在位置から再接続する"""
        position = self.get_timefree_position_seconds()
        duration = self.get_timefree_duration_seconds()
        return_code = self._player.getReturnCode()
        # 推定位置はバッファ待ちの分だけ実際より遅れるため、終端判定には余裕を持たせる
        if return_code == 0 and duration > 0 and position >= duration - TIMEFREE_END_MARGIN_SEC:
            self.log.info(f"timefree playback reached end: position={position}, duration={duration}")
            self.stop_timefree(announce_text="聴き逃し再生終了", resume_seconds=0)
            return

        now = time.monotonic()
        last = self._timefree_last_reconnect_monotonic
        if last is not None and now - last > TIMEFREE_RECONNECT_RESET_SEC:
            self._timefree_reconnect_count = 0
        if self._timefree_reconnect_count >= TIMEFREE_RECONNECT_MAX:
            self.log.error(
                f"timefree playback stopped unexpectedly and reconnect gave up: "
                f"returncode={return_code}, last error={(self._player.getLastError() or '')[:300]}"
            )
            self.stop_timefree(announce_text="聴き逃し再生が中断されました", resume_seconds=position)
            return
        self._timefree_reconnect_count += 1
        self._timefree_last_reconnect_monotonic = now
        self.log.warning(
            f"timefree playback stopped unexpectedly (returncode={return_code}); "
            f"reconnecting at {position}s ({self._timefree_reconnect_count}/{TIMEFREE_RECONNECT_MAX})"
        )
        # トークン期限切れで終了した可能性もあるため、再接続時は認証し直す
        self._apply_timefree_seek_target(position, refresh_auth=True)

    def onTimefreeSeekChanged(self, event):
        if self._timefree_seek_updating_ui:
            return
        if not self.is_timefree_playing():
            return
        target = int(self.timefree_seek_slider.GetValue())
        self.timefree_seek_label.SetLabel(
            f"{self._format_hhmmss(target)} / "
            f"{self._format_hhmmss(max(self.timefree_seek_slider.GetMax(), 1))}"
        )
        if not self.timefreeSeekApplyTimer.IsRunning():
            # 最初の操作は待たずに即反映する
            self._pending_seek_seconds = None
            self._last_requested_seek_seconds = target
            self._apply_timefree_seek_target(target)
        elif target != self._last_requested_seek_seconds:
            # 続く操作（ドラッグ・キーリピート）はまとめ、落ち着いたら最後の位置を1回だけ反映する
            self._pending_seek_seconds = target
        self.timefreeSeekApplyTimer.Start(200, oneShot=True)

    def _apply_timefree_seek_target(self, target, refresh_auth=False):
        if target is None or not self.is_timefree_playing():
            return
        self._schedule_timefree_seek_apply(int(target), refresh_auth)

    def _schedule_timefree_seek_apply(self, target, refresh_auth=False):
        with self._seek_apply_lock:
            self._seek_apply_latest = (int(target), self._playback_generation, refresh_auth)
            if self._seek_apply_running:
                return
            self._seek_apply_running = True
        threading.Thread(target=self._timefree_seek_apply_worker, daemon=True).start()

    def _timefree_seek_apply_worker(self):
        while True:
            with self._seek_apply_lock:
                latest = self._seek_apply_latest
                self._seek_apply_latest = None
                if latest is None:
                    self._seek_apply_running = False
                    return
            target, generation, refresh_auth = latest
            try:
                self.seek_timefree(target, generation=generation, refresh_auth=refresh_auth)
            except Exception as e:
                self.log.error(f"Failed to seek timefree playback: {e}")
            wx.CallAfter(self._sync_timefree_seek_ui_from_player)

    def onTimefreeSeekKeyDown(self, event):
        if not self.is_timefree_playing():
            event.Skip()
            return
        key = event.GetKeyCode()
        if key not in (wx.WXK_PAGEUP, wx.WXK_PAGEDOWN):
            event.Skip()
            return

        step = 60
        current = int(self.timefree_seek_slider.GetValue())
        max_value = int(max(self.timefree_seek_slider.GetMax(), 0))
        if key == wx.WXK_PAGEUP:
            target = max(0, current - step)
        else:
            target = min(max_value, current + step)

        self.timefree_seek_slider.SetValue(target)
        self._pending_seek_seconds = target
        self.timefree_seek_label.SetLabel(
            f"{self._format_hhmmss(target)} / "
            f"{self._format_hhmmss(max(max_value, 1))}"
        )
        # PgUp/PgDn は即時反映（タイマー待ちなし）
        self._pending_seek_seconds = None
        self._last_requested_seek_seconds = target
        self._apply_timefree_seek_target(target)

    def _on_timefree_seek_apply_timer(self, event):
        target = self._pending_seek_seconds
        self._pending_seek_seconds = None
        if target is None or target == self._last_requested_seek_seconds:
            return
        self._last_requested_seek_seconds = target
        self._apply_timefree_seek_target(target)

    def is_live_playing(self):
        """ライブ再生中かどうか"""
        return self.playback_mode == "live" and self.events.playing

    def clear_timefree_menu_live_ack(self):
        """ライブ×別局聴き逃し再開用の「番組表で過去番組を選んだ」フラグを消す"""
        self._timefree_menu_live_ack_station_id = None

    def set_timefree_menu_live_ack(self, station_id):
        """番組表で過去番組を選択したとき、対象局を記録する"""
        self._timefree_menu_live_ack_station_id = station_id

    def should_enable_timefree_menu_command(self):
        """
        メニュー「聴き逃し配信の再生/停止」と F2 の有効条件。
        - 聴き逃し再生中: 停止のため有効
        - 再開情報なし: 無効（新規開始は番組表ダイアログから）
        - 無再生かつ再開情報あり: 前回停止位置からの再開が可能なため有効
        - ライブ再生中かつ再開情報あり:
            - ライブの局と前回聴き逃しの局が同一 → 有効（確認後に再開）
            - 異なる局のライブ中 → 現在ライブの局の番組表で過去番組を選択するまで無効
        """
        if self.is_timefree_playing():
            return True
        if not self.has_last_timefree_request():
            return False
        if not self.is_live_playing():
            return True
        live_sid = self.current_station_id
        last_sid = (self._last_timefree_request or {}).get("station_id")
        if live_sid and last_sid == live_sid:
            return True
        ack = self._timefree_menu_live_ack_station_id
        return bool(live_sid and ack == live_sid)

    def update_timefree_command_ui(self):
        """聴き逃し配信メニュー・ショートカット(F2)のラベルと有効状態を更新"""
        if not hasattr(self.parent, "menu"):
            return
        try:
            is_tf = self.is_timefree_playing()
            label = _("聴き逃し停止") if is_tf else _("聴き逃し再生")
            self.parent.menu.SetMenuLabel("FUNCTION_TIMEFREE_TOGGLE", label)
            self.parent.menu.EnableMenu(
                "FUNCTION_TIMEFREE_TOGGLE",
                self.should_enable_timefree_menu_command(),
            )
        except Exception as e:
            self.log.error(f"Failed to update timefree command UI: {e}")
