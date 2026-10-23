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


# 聴き逃し再生の終了判定の余裕(秒)
TIMEFREE_END_MARGIN_SEC = 60
# 聴き逃しの再生位置は mpv から毎秒取得する。取得の間はこの秒数まで経過時間で進める
# （途切れて mpv が止まっている間に位置を進めないため）
TIMEFREE_POSITION_MAX_GAP_SEC = 3
# 聴き逃しの再接続後、mpv がこの秒数終了しなければ聴けているとみなす
TIMEFREE_RECONNECT_CHECK_SEC = 2
TIMEFREE_RECONNECTED_SEC = 3
# 聴き逃しシークスライダーの位置を動かすキー
SEEK_SLIDER_MOVE_KEYS = (
    wx.WXK_LEFT, wx.WXK_RIGHT, wx.WXK_UP, wx.WXK_DOWN,
    wx.WXK_HOME, wx.WXK_END, wx.WXK_PAGEUP, wx.WXK_PAGEDOWN,
)
# 再生（ライブ・聴き逃し）が途切れたときの自動再接続の間隔(秒)。回数がこれを超えたら最後の値で待つ
RECONNECT_INTERVAL_SEC = (5, 10, 20, 30)
# 途切れてからこの秒数つながらなければ再接続をあきらめる（回線の切り替え・ルーターの再起動などを待つ）
RECONNECT_GIVE_UP_SEC = 300
# この秒数以上安定して再生できたら、再接続の状態をリセットする
RECONNECT_RESET_SEC = 60
# この回数目の再接続から、途切れていることを読み上げる（一瞬の途切れでは読み上げない）
RECONNECT_ANNOUNCE_COUNT = 2
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
        # 聴き逃しの再生位置の計算。mpv から取得した time-pos の差分で、実際に再生した秒数を数える
        # （開始・シーク・mpv の再起動のたびに世代を進め、古い mpv の値を使わない）
        self._timefree_clock_epoch = 0
        self._timefree_played_sec = None
        self._timefree_last_time_pos = None
        self._timefree_sample_monotonic = None
        self._timefree_sampling = False
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
        # シーク位置の読み上げ。キーリピート中は最後の位置だけ読む
        self._seek_announce_call = None
        # シークスライダーを隠すときにフォーカスを移したか。直後の読み上げを遅らせるのに使う
        self._seek_focus_moved = False
        # 放送局一覧の更新（別スレッド）の状態と、終わったときに呼ぶ処理
        self._station_reload_running = False
        self._station_reload_again = False
        self._station_reload_callbacks = []
        # シーク(mpv 再起動)と再生開始/停止の競合を防ぐ。世代は再生開始/停止ごとに進める。
        self._playback_lock = threading.RLock()
        self._playback_generation = 0
        # 聴き逃し再生中に mpv が途中終了したときの自動再接続の状態
        self._timefree_recovery_running = False
        self._timefree_reconnect_count = 0
        self._timefree_last_reconnect_monotonic = None
        self._timefree_outage_started_monotonic = None
        self._timefree_next_reconnect_monotonic = None
        self._timefree_outage_announced = False
        self._timefree_relogged_in = False
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
        """ラジオ局リストを設定（起動時。取得が終わるまで待つ）"""
        self._build_station_tree(self._fetch_station_list(self.area, premium.session.is_areafree()))

    def _fetch_station_list(self, area, areafree):
        """放送局一覧を取得する。画面を操作しないので、別スレッドからも呼べる。

        Returns:
            ("areafree", [(地方名, 自分の地方か, [(局名, 局ID), ...]), ...])
            ("area", [(局名, 局ID), ...])
            ("error", 表示するメッセージ（None なら表示しない）)
        """
        # 認証で得たエリアコードの放送局一覧を取得（再生可能局のみ）
        if not area:
            return ("error", "エリア情報の取得に失敗しました。\nインターネットの接続状況をご確認ください")

        if areafree:
            regions = self._fetch_areafree_regions(area)
            if regions:
                return ("areafree", regions)

        url = f"https://radiko.jp/v2/station/list/{area}.xml"
        success, result = self.get_radio_stations(url)
        if not success:
            # v2失敗時のみ従来のv3全体リストをフォールバックとして使用
            self.log.warning(f"Failed to fetch area station list ({area}) via v2 API: {result}")
            fallback_url = "https://radiko.jp/v3/station/region/full.xml"
            success, result = self.get_radio_stations(fallback_url)
            if not success:
                return ("error", result)

        stations = []
        try:
            # XMLのパース
            parsed = ET.fromstring(result.encode('utf-8'))

            station_elements = parsed.findall(".//station")
            if station_elements:
                # v2/station/list/JPxx.xml 形式
                for station in station_elements:
                    name_element = station.find("name")
                    id_element = station.find("id")
                    if name_element is None or id_element is None:
                        continue
                    station_name = name_element.text or ""
                    station_id = id_element.text or ""
                    if not station_name or not station_id:
                        continue
                    stations.append((station_name, station_id))
            else:
                # v3/station/region/full.xml 形式（フォールバック）。全国の局と、自分の地方の局を載せる
                for r in parsed:
                    ascii_name = r.attrib["ascii_name"]
                    if ascii_name != "ZENKOKU" and ascii_name != self.region[area]:
                        continue
                    for station in r:
                        stations.append((station.find("name").text, station.find("id").text))

        except ET.ParseError:
            self.log.error("Failed to parse xml!")
            return ("error", "放送局情報の取得に失敗しました。\nしばらく時間をおいて再度お試しください。")

        except Exception as e:
            self.log.error(f"An unexpected error occurred: {str(e)}")
            return ("error", None)

        return ("area", stations)

    def _fetch_areafree_regions(self, area):
        """エリアフリー時、全国の放送局を地方ごとに分けて返す。画面を操作しないので、別スレッドからも呼べる。

        失敗した場合は None を返し、呼び出し側で通常のエリア内一覧にフォールバックする。
        """
        success, result = self.get_radio_stations("https://radiko.jp/v3/station/region/full.xml")
        if not success:
            self.log.warning(f"Failed to fetch full station list for areafree: {result}")
            return None
        try:
            parsed = ET.fromstring(result.encode('utf-8'))
        except ET.ParseError:
            self.log.error("Failed to parse full station list xml for areafree")
            return None

        # エリアフリー非対応（<areafree>0</areafree>。NHK の地域局など）の局は、エリア内で聴ける局だけ載せる
        local_ids = programmanager.get_area_station_ids(area)
        seen_ids = set()
        regions = []
        own_region_found = False
        for region in parsed:
            region_label = region.get("region_name") or region.get("ascii_name") or ""
            is_zenkoku = region.get("ascii_name") == "ZENKOKU"
            stations = []
            is_own_region = False
            for station in region.findall("station"):
                name_element = station.find("name")
                id_element = station.find("id")
                if name_element is None or id_element is None:
                    continue
                station_name = name_element.text or ""
                station_id = id_element.text or ""
                if not station_name or not station_id or station_id in seen_ids:
                    continue
                area_element = station.find("area_id")
                areafree_element = station.find("areafree")
                if areafree_element is not None and (areafree_element.text or "").strip() == "0":
                    if local_ids is not None:
                        listenable = station_id in local_ids
                    else:
                        listenable = area_element is not None and area_element.text == area
                    if not listenable:
                        continue
                stations.append((station_name, station_id))
                seen_ids.add(station_id)
                if not own_region_found and not is_zenkoku and area_element is not None and area_element.text == area:
                    is_own_region = True
                    own_region_found = True
            if stations:
                regions.append((region_label, is_own_region, stations))

        if not regions:
            self.log.warning("Full station list for areafree is empty")
            return None
        return regions

    def _build_station_tree(self, station_list, take_focus=True):
        """_fetch_station_list で取得した放送局一覧をツリーに描く（UI スレッド）

        take_focus が False のときはツリーへフォーカスを移さない（別の画面を操作中のとき）。
        """
        root = self.tree.GetRootItem()
        self.stid = {}
        self.station_list_areafree = False

        kind, data = station_list
        if kind == "error":
            if data:
                errorDialog(_(data))
            if take_focus:
                self.tree.SetFocus()
            self.tree.Expand(root)
            self.tree.SelectItem(root, select=True)
            return

        if kind == "areafree":
            own_region_item = None
            for region_label, is_own_region, stations in data:
                region_item = self.tree.AppendItem(root, region_label)
                for station_name, station_id in stations:
                    self.tree.AppendItem(region_item, station_name, data=station_id)
                    # 番組キャッシュ（番組検索）も self.stid を参照するため、全国の局が収集対象になる
                    self.stid[station_id] = station_name
                if is_own_region:
                    own_region_item = region_item
            if own_region_item is not None:
                self.tree.Expand(own_region_item)
            self.station_list_areafree = True
            self.log.info(f"areafree station list: {len(self.stid)} stations")
        else:
            for station_name, station_id in data:
                self.tree.AppendItem(root, station_name, data=station_id)
                self.stid[station_id] = station_name

        # イベントバインドとツリーの設定
        self.tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self.events.onRadioActivated)
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.events.onRadioSelected)
        if take_focus:
            self.tree.SetFocus()
        self.tree.Expand(root)
        self.tree.SelectItem(root, select=True)

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
            if last is not None and now - last > RECONNECT_RESET_SEC:
                self.log.info("live playback stable; reset reconnect state")
                self._reset_live_reconnect_state()
            return
        last_error = self._player.getLastError() or ""
        return_code = self._player.getReturnCode()
        generation = self._playback_generation
        station_id = self.current_station_id
        if self._live_outage_started_monotonic is None:
            self._live_outage_started_monotonic = now
        if now - self._live_outage_started_monotonic >= RECONNECT_GIVE_UP_SEC:
            self.log.warning(
                f"live playback could not be restored for {RECONNECT_GIVE_UP_SEC}s "
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
        interval = RECONNECT_INTERVAL_SEC[min(self._live_reconnect_count, len(RECONNECT_INTERVAL_SEC)) - 1]
        self._live_next_reconnect_monotonic = now + interval
        self.log.warning(
            f"live playback stopped unexpectedly (returncode={return_code}); reconnecting "
            f"(attempt {self._live_reconnect_count}, next in {interval}s): station={station_id}, "
            f"last player error={last_error[:300] or '(none)'}"
        )
        if self._live_reconnect_count == RECONNECT_ANNOUNCE_COUNT:
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
        # つながるまで数秒かかることがあるため、操作を受け付けたことを先に知らせる
        self._announce_playback_change(f"接続中: {self.stid.get(id, id)}")
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
            self._announce_playback_change(f"再生開始: {station_name}")
        except Exception as e:
            self.log.error(f"Failed to announce playback start: {e}")

    def play_timefree(self, sources, station_id=None, announce_text=None, resume_seconds=0,
                      timefree_info=None, error_message=None, on_finished=None):
        """聴き逃し再生を開始する。

        sources は (再生 URL とヘッダーを返す関数, stream_type) の並び。先頭から順に試し、最初に再生できたものを使う。
        関数が None を返したら、その配信は試さずに次へ進む。
        認証・mpv の起動は別スレッドで行い、すぐに戻る（通信が遅くても画面を止めない）。
        開始できなかった場合は停止状態に戻し、error_message に理由を添えてエラーダイアログで知らせる。
        on_finished は開始の成否 (bool) を UI スレッドで受け取る（準備中に停止・切替があったときは呼ばない）。
        """
        if self._shutting_down:
            self.log.info(f"timefree playback request ignored during shutdown: station={station_id}")
            return
        self.log.info(
            f"timefree playback requested: station={station_id}, resume={resume_seconds}s, "
            f"areafree={premium.session.is_areafree()}"
        )
        # 前の再生を止めて停止状態にする（準備中に別の再生・停止をしたら、この要求は取りやめる）
        self._reset_playback_state()
        generation = self._playback_generation
        resume_seconds = max(0, int(resume_seconds or 0))
        self._timefree_resume_position_sec = resume_seconds
        # 開始できなくても再開位置などの再生情報は残し、メニューから再試行できるようにする
        self._last_timefree_request = {
            "stream_url": None,
            "station_id": station_id,
            "announce_text": announce_text,
            "headers": {},
            "resume_seconds": resume_seconds,
            "timefree_info": dict(timefree_info or {}),
            "ft_dt": (timefree_info or {}).get("ft_dt"),
            "to_dt": (timefree_info or {}).get("to_dt"),
            "stream_type": (timefree_info or {}).get("stream_type", "b"),
        }
        self.update_timefree_command_ui()
        if announce_text:
            # つながるまで数秒かかることがあるため、操作を受け付けたことを先に知らせる
            # （announce_text が無いのは、利用者の操作によらない内部からの開始）
            self._announce_playback_change(f"接続中: {self.stid.get(station_id, station_id)}")
        volume = self.volume.GetValue()
        threading.Thread(
            target=self._timefree_start_worker,
            args=(generation, list(sources), station_id, volume, error_message, on_finished),
            name="TimefreeStart",
            daemon=True,
        ).start()

    def _is_playback_changed(self, generation):
        """generation の時点から、停止・別の再生への切替・終了があったか"""
        return self._shutting_down or generation != self._playback_generation

    def _start_timefree_player(self, generation, stream_url, headers, volume):
        """（別スレッド）聴き逃しの mpv を起動する。起動したら True、停止・切替済みなら None"""
        # 停止・切替との競合を防ぐため、世代の確認と mpv の起動はロック内で行う
        with self._playback_lock:
            if self._is_playback_changed(generation):
                return None
            if self._player.isPlaying():
                # 先に試した配信の mpv が残っていれば止める
                self._player.stop()
            self.m3u8 = stream_url
            self._player.setNonSeekableInput(True)
            self._player.setHttpHeaders(headers or {})
            # 再開位置付きの URL は5秒区間の先頭から始まるため、区間内の残りを読み飛ばす
            req = self._last_timefree_request or {}
            start_sec = 0
            if req.get("ft_dt") and req.get("to_dt"):
                start_sec = self._get_program_manager().timefree_seek_segment_offset(
                    req["ft_dt"], req["to_dt"], req.get("resume_seconds", 0)
                )
            self._player.setStartPosition(start_sec)
            self._player.setSource(stream_url)
            self._player.setVolume(volume)
            self.log.info("playing...")
            return self._player.play()

    def _timefree_start_worker(self, generation, sources, station_id, volume, error_message, on_finished):
        """（別スレッド）配信を順に試して mpv を起動し、結果を UI スレッドへ渡す"""
        reason = None
        for fetch, stream_type in sources:
            if self._is_playback_changed(generation):
                return
            try:
                source = fetch()
                if source is None:
                    continue
                stream_url, headers = source
                if not stream_url:
                    raise RuntimeError("empty stream url")
            except Exception as e:
                self.log.error(f"Failed to get timefree stream url: station={station_id}, type={stream_type}: {e!r}")
                reason = describe_playback_exception(e)
                continue
            started_at = time.monotonic()
            started = self._start_timefree_player(generation, stream_url, headers, volume)
            if started is None:
                self.log.info(f"timefree playback start discarded (playback changed): station={station_id}")
                return
            if started:
                # 配信を取得できず mpv がすぐ終了する場合を見分けるため、少し待ってから確かめる
                time.sleep(0.9)
                if self._is_playback_changed(generation):
                    return
                started = self._player.isPlaying()
            if started:
                wx.CallAfter(
                    self._finish_timefree_start, generation, station_id, error_message, on_finished,
                    True, None, stream_url, headers, stream_type, started_at,
                )
                return
            reason = self._diagnose_stream_failure(stream_url, headers)
            self.log.error(
                f"Failed to start timefree playback: station={station_id}, type={stream_type}, reason={reason}, "
                f"player error={(self._player.getLastError() or '')[:300]}"
            )
        wx.CallAfter(
            self._finish_timefree_start, generation, station_id, error_message, on_finished,
            False, reason or "再生 URL を取得できませんでした。", None, None, None, None,
        )

    def _finish_timefree_start(self, generation, station_id, error_message, on_finished,
                               started, reason, stream_url, headers, stream_type, started_at):
        """（UI スレッド）聴き逃し再生の開始結果を画面と状態に反映する"""
        if self._is_playback_changed(generation):
            # 起動中に停止・別の再生への切替があった（mpv は停止・切替側で止めている）
            self.log.info(f"timefree playback start result discarded (playback changed): station={station_id}")
            return
        if not started:
            self._reset_playback_state()
            self._notify_timefree_start_finished(on_finished, False)
            errorDialog((error_message or _("聴き逃し再生に失敗しました。")) + f"\n{reason}")
            return

        req = self._last_timefree_request
        req["stream_url"] = stream_url
        req["headers"] = headers or {}
        req["stream_type"] = stream_type
        req["timefree_info"]["stream_type"] = stream_type
        self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("再生"))
        self.current_station_id = station_id
        self.events.current_playing_station_id = station_id
        self.current_progs = None
        self.playback_mode = "timefree"
        self._reset_timefree_reconnect_state()
        self._reset_timefree_clock(req["resume_seconds"], started_at)
        self._timefree_info = dict(req["timefree_info"])
        self._timefree_duration_sec = max(0, int(self._timefree_info.get("duration_sec", 0) or 0))
        self.events.playing = False
        self.log.info(f"timefree playback started: station={station_id}, position={req['resume_seconds']}s")
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
            if req.get("announce_text"):
                self.parent.app.say(req["announce_text"], interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce timefree playback start: {e}")
        self._notify_timefree_start_finished(on_finished, True)

    def _notify_timefree_start_finished(self, on_finished, started):
        """聴き逃し再生の開始結果を呼び出し元へ知らせる"""
        if on_finished is None:
            return
        try:
            on_finished(started)
        except Exception as e:
            # 呼び出し元のダイアログが閉じられた場合など
            self.log.warning(f"timefree playback start callback failed: {e!r}")

    def play_timefree_program(self, progs, station_id, start_dt, end_dt, announce_text, timefree_info,
                              error_message=None, on_finished=None):
        """番組の聴き逃し再生を始める。通常の配信 (type=b) で再生できなければ互換の配信 (type=c) で試す"""
        def compat_source():
            if progs.uses_areafree(station_id):
                # エリアフリーでは最初から type=c のため、互換ソースで再試行しても結果は同じ
                return None
            self.log.warning(f"timefree playback primary source failed; retrying with compat source (type=c): station={station_id}")
            return progs.get_timefree_playback_source_compat(station_id, start_dt, end_dt)

        self.play_timefree(
            [
                (lambda: progs.get_timefree_playback_source(station_id, start_dt, end_dt), "b"),
                (compat_source, "c"),
            ],
            station_id=station_id,
            announce_text=announce_text,
            resume_seconds=0,
            timefree_info=timefree_info,
            error_message=error_message,
            on_finished=on_finished,
        )

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
        now = time.monotonic()
        elapsed = 0
        if self._timefree_played_sec is not None:
            # 最後に mpv から位置を取得してからの分は、途切れていても進み過ぎないよう上限を設ける
            gap = max(0, now - self._timefree_sample_monotonic)
            elapsed = self._timefree_played_sec + min(gap, TIMEFREE_POSITION_MAX_GAP_SEC)
        elif self._timefree_started_monotonic:
            # mpv から位置を取得するまでは、開始からの経過時間で数える
            elapsed = max(0, now - self._timefree_started_monotonic)
        pos = int(max(0, self._timefree_resume_position_sec + elapsed))
        if self._timefree_duration_sec > 0:
            pos = min(pos, self._timefree_duration_sec)
        return pos

    def _reset_timefree_clock(self, position, started_at=None, running=True):
        """聴き逃しの再生位置の計算を position 秒から始め直す（開始・シーク・mpv の再起動・停止のたび）。
        running が False なら、位置を position で止める。
        """
        self._timefree_clock_epoch += 1
        self._timefree_resume_position_sec = position
        if running:
            self._timefree_started_monotonic = time.monotonic() if started_at is None else started_at
        else:
            self._timefree_started_monotonic = None
        self._timefree_played_sec = None
        self._timefree_last_time_pos = None
        self._timefree_sample_monotonic = None

    def _freeze_timefree_clock(self):
        """mpv が終了したとき、再生位置をその時点で止める（再接続を待つ間に位置を進めない）"""
        if self._timefree_played_sec is None:
            # mpv から位置を取得する前に終了した。ほとんど再生できていないため、開始位置のままにする
            position = self._timefree_resume_position_sec
        else:
            position = self.get_timefree_position_seconds()
        self._reset_timefree_clock(position, running=False)

    def _sample_timefree_position(self):
        """（UI スレッド）mpv から実際の再生位置を別スレッドで取得する。
        途切れて読み込みを待つ間は mpv の位置が進まないため、その間を再生位置に数えずに済む。
        """
        if self._timefree_sampling:
            return
        self._timefree_sampling = True
        epoch = self._timefree_clock_epoch

        def run():
            try:
                time_pos = self._player.getProperty("time-pos")
                if not isinstance(time_pos, (int, float)):
                    time_pos = None
                with self._playback_lock:
                    if epoch == self._timefree_clock_epoch and self.is_timefree_playing():
                        self._apply_timefree_time_pos(time_pos)
            except Exception as e:
                self.log.warning(f"Failed to get timefree playback position: {e!r}")
            finally:
                self._timefree_sampling = False

        threading.Thread(target=run, name="TimefreePosition", daemon=True).start()

    def _apply_timefree_time_pos(self, time_pos):
        """mpv の time-pos（取得できなければ None）から、実際に再生した秒数を進める"""
        now = time.monotonic()
        if self._timefree_played_sec is None:
            if time_pos is None or self._timefree_started_monotonic is None:
                return
            # 最初に取得できるまでは、開始からの経過時間で数える
            self._timefree_played_sec = max(0.0, now - self._timefree_started_monotonic)
        else:
            wall = max(0.0, now - self._timefree_sample_monotonic)
            delta = None
            if time_pos is not None and self._timefree_last_time_pos is not None:
                delta = time_pos - self._timefree_last_time_pos
            if delta is None or delta < 0 or delta > wall + 1:
                # 位置を取得できなかった・値が飛んだ。この間は経過時間で数える
                delta = wall
            self._timefree_played_sec += delta
        self._timefree_last_time_pos = time_pos
        self._timefree_sample_monotonic = now

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
        # 配信は5秒区間の先頭から始まるため、区間内の残りを mpv で読み飛ばして target から再生する
        start_sec = pm.timefree_seek_segment_offset(ft_dt, to_dt, target)
        self.log.info(f"timefree seek: target={target}s, start_sec={start_sec}, refresh_auth={refresh_auth}")
        result = self._restart_timefree_player_at(generation, req, stream_url, headers, target, start_sec)
        if result is False:
            raise RuntimeError(
                f"タイムフリーシークに失敗しました: {self._player.getLastError() or 'unknown'}"
            )

    def _restart_timefree_player_at(self, generation, req, stream_url, headers, target, start_sec):
        """シーク先 URL で mpv を起動し直す（URL の先頭から start_sec 秒読み飛ばす）。

        戻り値: 起動成功 True / 失敗 False / 停止・切替済みで何もしなかった None
        """
        with self._playback_lock:
            if generation != self._playback_generation or not self.is_timefree_playing():
                return None
            self._player.setNonSeekableInput(True)
            if not self._player.restartWithSource(stream_url, headers, start_sec):
                return False
            req["stream_url"] = stream_url
            req["headers"] = headers
            req["resume_seconds"] = target
            self.m3u8 = stream_url
            self._reset_timefree_clock(target)
            return True

    def _timefree_restart_source(self):
        """音量/デバイス変更で mpv が再起動されるとき、現在位置から再開する (URL, ヘッダー, 読み飛ばす秒数) を返す。

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
        pm = self._get_program_manager()
        stream_url = pm.build_timefree_seek_url(
            station_id, ft_dt, to_dt, target, req.get("stream_type", "b")
        )
        headers = req.get("headers") or {}
        req["stream_url"] = stream_url
        req["resume_seconds"] = target
        self.m3u8 = stream_url
        self._reset_timefree_clock(target)
        return stream_url, headers, pm.timefree_seek_segment_offset(ft_dt, to_dt, target)

    def _replay_timefree_at(self, target_seconds, announce=False):
        """seek秒位置でURLを再生成してタイムフリー再生（認証・起動は別スレッドで行う）"""
        req = self._last_timefree_request or {}
        ft_dt = req.get("ft_dt")
        to_dt = req.get("to_dt")
        station_id = req.get("station_id")
        stream_type = req.get("stream_type", "b")
        info = dict(req.get("timefree_info") or {})
        info["stream_type"] = stream_type

        if ft_dt and to_dt and station_id:
            pm = self._get_program_manager()

            def fetch():
                return pm.get_timefree_playback_source_with_seek(
                    station_id=station_id,
                    ft_dt=ft_dt,
                    to_dt=to_dt,
                    seek_seconds=target_seconds,
                    stream_type=stream_type,
                )
        else:
            stream_url = req.get("stream_url")
            headers = req.get("headers")

            def fetch():
                return stream_url, headers

        self.play_timefree(
            [(fetch, stream_type)],
            station_id=station_id,
            announce_text=req.get("announce_text") if announce else None,
            resume_seconds=target_seconds,
            timefree_info=info,
            error_message=_("聴き逃し再生の再開に失敗しました。"),
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
        self._reset_timefree_clock(current_pos, running=False)
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
            self._announce_playback_change(announce_text)
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
            self._announce_playback_change("再生停止")
        except Exception as e:
            self.log.error(f"Failed to announce playback stop: {e}")

    def update_program_info(self):
        """番組情報更新タイマーを開始"""
        self.updateInfoTimer.Start(self.tmg.replace_milliseconds(3))
        self.updateInfoTimer.Bind(wx.EVT_TIMER, self.events.onUpdateProcess)

    def get_latest_programList(self, progs, on_finished=None):
        """番組リストを最新に更新する。

        認証と放送局一覧の取得には時間がかかる（回線が悪いと数分）ため、別スレッドで行い画面を止めない。
        on_finished は、成否にかかわらず一覧の更新が終わったときに UI スレッドで呼ばれる。
        """
        if on_finished is not None:
            self._station_reload_callbacks.append(on_finished)
        if self._station_reload_running:
            # 取得中にログイン状態が変わった可能性があるため、終わったらもう一度取得し直す
            self._station_reload_again = True
            self.app.say(_("放送局一覧を更新しています"), interrupt=True)
            return
        self._station_reload_running = True
        self._station_reload_again = False
        # 「再生停止」などの直前の読み上げを切らないよう、割り込まずに続けて読む
        self.app.say(_("放送局一覧を更新しています"))
        areafree = premium.session.is_areafree()
        threading.Thread(
            target=self._station_reload_worker,
            args=(progs, areafree),
            name="StationListReload",
            daemon=True,
        ).start()

    def _station_reload_worker(self, progs, areafree):
        """（別スレッド）エリア判定と放送局一覧の取得を行い、結果を UI スレッドへ渡す"""
        try:
            area = progs.getArea()
        except Exception as e:
            self.log.error(f"Failed to determine area while reloading station list: {e!r}")
            wx.CallAfter(self._finish_station_list_reload, progs, None, None)
            return
        try:
            station_list = self._fetch_station_list(area, areafree)
        except Exception as e:
            self.log.error(f"Failed to fetch station list: {e!r}")
            station_list = ("error", None)
        wx.CallAfter(self._finish_station_list_reload, progs, area, station_list)

    def _finish_station_list_reload(self, progs, area, station_list):
        """（UI スレッド）取得した放送局一覧でツリーを作り直す"""
        self._station_reload_running = False
        if self._shutting_down:
            return
        if self._station_reload_again:
            # 取得中にもう一度更新を求められた。古い結果は捨てて取得し直す
            self.get_latest_programList(progs)
            return

        if area is None:
            # 認証（エリア判定）に失敗したら今の一覧を残す（消してから失敗すると一覧が空になる）
            errorDialog(_("エリア情報の取得に失敗しました。\nインターネットの接続状況をご確認ください"))
        else:
            self.area = area
            was_displaying = self.events.displaying
            # 作り直したあと、直前に選んでいた局（無ければ再生中の局）を選び直す
            restore_station_id = self._get_selected_station_id() or self.current_station_id
            self.tree.DeleteAllItems()
            # 放送局リスト更新時は番組情報UIを一旦破棄し、メニューを一時的に無効にする。
            self.parent.program_info_handler.destroy_program_info_ui(disable_menu=True)
            self.creator.GetSizer().Layout()
            self.setupradio()
            # 取得中にユーザーが別の画面（ダイアログなど）へ移っていたら、フォーカスを奪わない
            self._build_station_tree(station_list, take_focus=self.parent.hFrame.IsActive())
            self._select_station_in_tree(restore_station_id)
            # 前回の聴き逃しの局が一覧から消えていれば（エリアフリーからのログアウトなど）再開できなくする
            self.update_timefree_command_ui()
            self._sync_program_info_menu_after_station_list_reload(was_displaying)
            self.creator.GetSizer().Layout()
            if station_list[0] != "error":
                # ツリーへのフォーカス移動の読み上げで消されないよう、少し遅らせる
                self.app.sayLater(_("放送局一覧を更新しました"))

        callbacks = self._station_reload_callbacks
        self._station_reload_callbacks = []
        for callback in callbacks:
            try:
                callback()
            except Exception as e:
                self.log.error(f"Error in station list reload callback: {e!r}")

    def _get_selected_station_id(self):
        """放送局一覧で選択中の局 ID を返す（ルートや未選択なら None）"""
        item = self.tree.GetSelection()
        if not item.IsOk() or item == self.tree.GetRootItem():
            return None
        return self.tree.GetItemData(item)

    def _select_station_in_tree(self, station_id):
        """放送局一覧で指定した局を選択する。見つからなければ何もしない"""
        if not station_id:
            return
        root = self.tree.GetRootItem()
        if not root.IsOk():
            return
        item = self._find_station_item(root, station_id)
        if item is not None:
            # エリアフリーの一覧は地域ごとの入れ子のため、閉じていても EnsureVisible で開く
            self.tree.EnsureVisible(item)
            self.tree.SelectItem(item)

    def _find_station_item(self, parent, station_id):
        item, cookie = self.tree.GetFirstChild(parent)
        while item.IsOk():
            if self.tree.GetItemData(item) == station_id:
                return item
            if self.tree.ItemHasChildren(item):
                found = self._find_station_item(item, station_id)
                if found is not None:
                    return found
            item, cookie = self.tree.GetNextChild(parent, cookie)
        return None

    def _sync_program_info_menu_after_station_list_reload(self, was_displaying):
        """放送局一覧の再構築後、番組情報の表示設定とメニューを整合させる"""
        handler = self.parent.program_info_handler
        menu = self.parent.menu
        if was_displaying:
            menu.SetMenuLabel("HIDE_PROGRAMINFO")
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

    def _format_spoken_time(self, seconds):
        """読み上げ用に「1時間2分3秒」の形にする"""
        total = int(max(0, seconds))
        hh = total // 3600
        mm = (total % 3600) // 60
        ss = total % 60
        text = ""
        if hh:
            text += f"{hh}時間"
        if hh or mm:
            text += f"{mm}分"
        return text + f"{ss}秒"

    def _announce_seek_position(self, target, duration):
        """シーク先の位置を読み上げる。スライダーの値（秒数）の読み上げより後に話すよう少し待つ"""
        text = f"{self._format_spoken_time(target)}、全体{self._format_spoken_time(duration)}"
        if self._seek_announce_call is not None and self._seek_announce_call.IsRunning():
            self._seek_announce_call.Stop()
        self._seek_announce_call = wx.CallLater(150, self.app.say, text, interrupt=True)

    def _announce_playback_change(self, text):
        """再生開始・停止を読み上げる。シークスライダーからフォーカスを移した直後は、
        移動先の読み上げで消されないよう少し遅らせる"""
        if self._seek_focus_moved:
            self._seek_focus_moved = False
            self.app.sayLater(text)
        else:
            self.app.say(text, interrupt=True)

    def _is_seek_slider_focused(self):
        return hasattr(self, "timefree_seek_slider") and wx.Window.FindFocus() is self.timefree_seek_slider

    def _set_timefree_seek_ui_visible(self, visible):
        if not hasattr(self, "timefree_seek_slider"):
            return
        if not visible and self._is_seek_slider_focused() and hasattr(self, "tree"):
            # 隠すスライダーにフォーカスが残ると、フォーカスの行き場がなくなる
            self.tree.SetFocus()
            self._seek_focus_moved = True
        self.timefree_seek_slider.Show(visible)
        self.timefree_seek_label.Show(visible)
        self.timefree_seek_slider.Enable(visible)
        try:
            self.parent.hPanel.Layout()
            self.parent.sizer.Layout()
        except Exception:
            pass

    def _sync_timefree_seek_ui_from_player(self, force=False):
        if not hasattr(self, "timefree_seek_slider"):
            return
        if not self.is_timefree_playing():
            return
        duration = self.get_timefree_duration_seconds()
        position = self.get_timefree_position_seconds()
        if duration <= 0:
            duration = max(position, 1)
        position = max(0, min(position, duration))
        if not force and self._is_seek_slider_focused():
            # フォーカス中に値を書き換えると、スクリーンリーダーが毎秒その値を読み上げてしまう。
            # 表示だけ更新し、スライダーの値はキー操作の直前に合わせる
            self.timefree_seek_label.SetLabel(
                f"{self._format_hhmmss(position)} / {self._format_hhmmss(duration)}"
            )
            return
        self._timefree_seek_updating_ui = True
        try:
            self.timefree_seek_slider.SetRange(0, int(duration))
            self.timefree_seek_slider.SetValue(int(position))
            self.timefree_seek_label.SetLabel(
                f"{self._format_hhmmss(position)} / {self._format_hhmmss(duration)}"
            )
            # スライダーが今の再生位置を指している。この値のままのイベントはシーク不要
            self._last_requested_seek_seconds = int(position)
        finally:
            self._timefree_seek_updating_ui = False

    def _is_timefree_seek_busy(self):
        """シーク操作中・反映中か（この間はスライダーを再生位置で上書きしない）"""
        return (
            self._pending_seek_seconds is not None
            or self._seek_apply_running
            or self.timefreeSeekApplyTimer.IsRunning()
        )

    def _on_timefree_seek_applied(self):
        if not self._is_timefree_seek_busy():
            self._sync_timefree_seek_ui_from_player()

    def _start_timefree_seek_timer(self):
        if not self.timefreeSeekTimer.IsRunning():
            self.timefreeSeekTimer.Start(1000)

    def _stop_timefree_seek_timer(self):
        if self.timefreeSeekTimer.IsRunning():
            self.timefreeSeekTimer.Stop()
        if self.timefreeSeekApplyTimer.IsRunning():
            self.timefreeSeekApplyTimer.Stop()

    def _on_timefree_seek_timer(self, event):
        if not self.is_timefree_playing():
            self._stop_timefree_seek_timer()
            return
        # 操作中・シーク反映中はスライダーを旧位置へ戻さない。再接続中は結果を待つ
        if self._is_timefree_seek_busy() or self._timefree_recovery_running:
            return
        if not self._player.isPlaying():
            self._handle_timefree_player_exit()
            return
        self._check_timefree_reconnected()
        self._sample_timefree_position()
        self._sync_timefree_seek_ui_from_player()

    def _reset_timefree_reconnect_state(self):
        self._timefree_reconnect_count = 0
        self._timefree_last_reconnect_monotonic = None
        self._timefree_outage_started_monotonic = None
        self._timefree_next_reconnect_monotonic = None
        self._timefree_outage_announced = False
        self._timefree_relogged_in = False

    def _check_timefree_reconnected(self):
        """（再生中）再接続後に再生が続いていれば、復帰を知らせ、安定したら再接続の状態をリセットする"""
        last = self._timefree_last_reconnect_monotonic
        if last is None:
            return
        now = time.monotonic()
        if self._timefree_outage_announced and now - last >= TIMEFREE_RECONNECTED_SEC:
            self._timefree_outage_announced = False
            try:
                self.parent.app.say("再接続しました。", interrupt=True)
            except Exception as e:
                self.log.error(f"Failed to announce timefree reconnected: {e}")
        if now - last > RECONNECT_RESET_SEC:
            self.log.info("timefree playback stable; reset reconnect state")
            self._reset_timefree_reconnect_state()

    def _handle_timefree_player_exit(self):
        """聴き逃し再生中に mpv が終了したとき、番組終了なら停止し、途中なら間隔を空けて再接続する"""
        # 再接続を待つ間に再生位置が進まないよう、終了した時点で止める
        self._freeze_timefree_clock()
        position = self.get_timefree_position_seconds()
        duration = self.get_timefree_duration_seconds()
        return_code = self._player.getReturnCode()
        # 位置は mpv から毎秒取得した値のため、取得の間隔の分だけ遅れる。終端判定には余裕を持たせる
        if return_code == 0 and duration > 0 and position >= duration - TIMEFREE_END_MARGIN_SEC:
            self.log.info(f"timefree playback reached end: position={position}, duration={duration}")
            self.stop_timefree(announce_text="聴き逃し再生終了", resume_seconds=0)
            return

        now = time.monotonic()
        if self._timefree_outage_started_monotonic is None:
            self._timefree_outage_started_monotonic = now
        if now - self._timefree_outage_started_monotonic >= RECONNECT_GIVE_UP_SEC:
            self.log.error(
                f"timefree playback could not be restored for {RECONNECT_GIVE_UP_SEC}s "
                f"({self._timefree_reconnect_count} reconnects): returncode={return_code}, "
                f"last error={(self._player.getLastError() or '')[:300]}"
            )
            self.stop_timefree(announce_text=f"聴き逃し再生が中断されました。{NETWORK_ERROR_MESSAGE}", resume_seconds=position)
            return
        if self._timefree_next_reconnect_monotonic is not None and now < self._timefree_next_reconnect_monotonic:
            return
        self._timefree_reconnect_count += 1
        self._timefree_last_reconnect_monotonic = now
        interval = RECONNECT_INTERVAL_SEC[min(self._timefree_reconnect_count, len(RECONNECT_INTERVAL_SEC)) - 1]
        self._timefree_next_reconnect_monotonic = now + interval
        self.log.warning(
            f"timefree playback stopped unexpectedly (returncode={return_code}); reconnecting at {position}s "
            f"(attempt {self._timefree_reconnect_count}, next in {interval}s), "
            f"last player error={(self._player.getLastError() or '')[:300] or '(none)'}"
        )
        if self._timefree_reconnect_count == RECONNECT_ANNOUNCE_COUNT:
            self._timefree_outage_announced = True
            try:
                self.parent.app.say("接続が途切れました。再接続しています。", interrupt=True)
            except Exception as e:
                self.log.error(f"Failed to announce timefree reconnecting: {e}")
        self._start_timefree_recovery(position)

    def _start_timefree_recovery(self, position):
        """（UI スレッド）聴き逃しの再接続を別スレッドで開始する（認証の通信で画面を止めない）"""
        generation = self._playback_generation
        self._timefree_recovery_running = True

        def run():
            try:
                self._timefree_reconnect_worker(generation, position)
            except Exception:
                self.log.exception("unexpected error in timefree reconnect")
            finally:
                self._timefree_recovery_running = False

        try:
            threading.Thread(target=run, name="TimefreeReconnect", daemon=True).start()
        except Exception:
            self._timefree_recovery_running = False
            self.log.exception("failed to start timefree reconnect thread")

    def _timefree_reconnect_worker(self, generation, position):
        """（別スレッド）認証し直して position 秒から再生し直す。

        失敗したら次の間隔で再試行する。聴取できない（403・404）とわかったら待っても直らないため中断する。
        """
        try:
            # トークン期限切れで終了した可能性もあるため、認証し直す
            self.seek_timefree(position, generation=generation, refresh_auth=True)
        except Exception as e:
            self.log.warning(f"timefree reconnect failed: {e!r}")
            return
        # 配信を読み込めないと mpv はすぐに終了する。終了したら原因を調べる
        time.sleep(TIMEFREE_RECONNECT_CHECK_SEC)
        if self._is_playback_changed(generation) or self._player.isPlaying():
            return
        req = self._last_timefree_request or {}
        status = self._probe_stream_status(req.get("stream_url"), req.get("headers"))
        if status == 403 and not self._timefree_relogged_in:
            if self._relogin_for_forbidden_stream(req.get("station_id"), self._get_program_manager()):
                # 次の再接続で新しいセッションで認証し直す
                self._timefree_relogged_in = True
                return
        if status in (403, 404):
            wx.CallAfter(self._finish_timefree_give_up, generation, self._describe_stream_status(status))

    def _finish_timefree_give_up(self, generation, reason):
        """（UI スレッド）聴き逃しの再接続を諦めて停止し、理由を読み上げる"""
        if self._is_playback_changed(generation) or not self.is_timefree_playing():
            return
        self.log.error(
            f"timefree playback gave up after {self._timefree_reconnect_count} reconnects: reason={reason}"
        )
        self.stop_timefree(
            announce_text=f"聴き逃し再生が中断されました。{reason}",
            resume_seconds=self.get_timefree_position_seconds(),
        )

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
        if target == self._last_requested_seek_seconds:
            # 位置が変わっていない（キーを離したときの EVT_SCROLL_CHANGED、往復して元に戻した等）。
            # ここでシークすると読み込み中のストリームを無駄に作り直し、再生がもたつく。
            self._pending_seek_seconds = None
            return
        self._announce_seek_position(target, max(self.timefree_seek_slider.GetMax(), 1))
        if not self.timefreeSeekApplyTimer.IsRunning():
            # 最初の操作は待たずに即反映する
            self._pending_seek_seconds = None
            self._last_requested_seek_seconds = target
            self._apply_timefree_seek_target(target)
        else:
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
            wx.CallAfter(self._on_timefree_seek_applied)

    def onTimefreeSeekKeyDown(self, event):
        if not self.is_timefree_playing():
            event.Skip()
            return
        key = event.GetKeyCode()
        if key in SEEK_SLIDER_MOVE_KEYS and not self._is_timefree_seek_busy():
            # フォーカス中はスライダーの値を再生位置に合わせていないため、動かす前に合わせる
            self._sync_timefree_seek_ui_from_player(force=True)
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
        if target == current:
            # 先頭/末尾で押し続けたときは何もしない
            return

        self.timefree_seek_slider.SetValue(target)
        self.timefree_seek_label.SetLabel(
            f"{self._format_hhmmss(target)} / "
            f"{self._format_hhmmss(max(max_value, 1))}"
        )
        self._announce_seek_position(target, max(max_value, 1))
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
        - 前回聴き逃しの局が今の放送局一覧に無い: 無効（エリア外になった局は再開できない）
        - 無再生かつ再開情報あり: 前回停止位置からの再開が可能なため有効
        - ライブ再生中かつ再開情報あり:
            - ライブの局と前回聴き逃しの局が同一 → 有効（確認後に再開）
            - 異なる局のライブ中 → 現在ライブの局の番組表で過去番組を選択するまで無効
        """
        if self.is_timefree_playing():
            return True
        if not self.has_last_timefree_request():
            return False
        if not self._is_last_timefree_station_listed():
            return False
        if not self.is_live_playing():
            return True
        live_sid = self.current_station_id
        last_sid = (self._last_timefree_request or {}).get("station_id")
        if live_sid and last_sid == live_sid:
            return True
        ack = self._timefree_menu_live_ack_station_id
        return bool(live_sid and ack == live_sid)

    def _is_last_timefree_station_listed(self):
        """前回の聴き逃しの局が今の放送局一覧にあるか。
        エリアフリーからのログアウトやセッション切れでエリア外になった局は再開すると失敗するため。
        一覧の取得に失敗して空のときは判定できないため、あるものとみなす。
        再開情報は消さないため、再びログインすれば再開できる。
        """
        if not self.stid:
            return True
        return (self._last_timefree_request or {}).get("station_id") in self.stid

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
