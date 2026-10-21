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
from simpleDialog import *
from views.mpvPlayer import MPVAudioPlayer
from views import premium
from views import programmanager


# 聴き逃し再生の終了判定の余裕(秒)と、途中終了時の自動再接続の上限
TIMEFREE_END_MARGIN_SEC = 60
TIMEFREE_RECONNECT_MAX = 3
TIMEFREE_RECONNECT_RESET_SEC = 60


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
        self._refresh_in_progress = False
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
                req.add_header('User-Agent', 'Mozilla/5.0')
                
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

        # 認証で得たエリアコードの放送局一覧を取得（再生可能局のみ）
        if not self.area:
            errorDialog(_("エリア情報の取得に失敗しました。\nインターネットの接続状況をご確認ください"))
            self.tree.SetFocus()
            self.tree.Expand(root)
            self.tree.SelectItem(root, select=True)
            return

        if premium.session.is_areafree() and self._append_areafree_stations(root):
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
                if region_item is None:
                    region_item = self.tree.AppendItem(root, region_label)
                self.tree.AppendItem(region_item, station_name, data=station_id)
                stid[station_id] = station_name
                area_element = station.find("area_id")
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

    def get_streamUrl(self, stationid, progs):
        """ストリームURLを取得"""
        self.m3u8, self.live_http_headers = progs.get_authenticated_stream_url(stationid)

    def _refresh_playback_stream(self, max_retries=3, force_reconnect=False):
        """再生中のストリームURLを再取得してプレイヤーへ反映"""
        if self._shutting_down:
            return False
        if not self.current_station_id or not self.current_progs:
            return False
        if self._refresh_in_progress:
            return False

        self._refresh_in_progress = True
        try:
            for attempt in range(max_retries):
                try:
                    self.get_streamUrl(self.current_station_id, self.current_progs)
                    if not self.m3u8:
                        raise RuntimeError("empty stream url")
                    self._player.setHttpHeaders(self.live_http_headers)
                    self._player.setSource(self.m3u8)
                    self._player.play()
                    return True
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    self.log.warning(f"stream refresh retry {attempt + 1}/{max_retries - 1}: {e}")
                    if force_reconnect:
                        try:
                            self._player.stop()
                        except Exception:
                            pass
                    time.sleep(0.5)
        finally:
            self._refresh_in_progress = False
        return False

    def _on_stream_watchdog_timer(self, event):
        """再生停止を検知して再認証・再接続する"""
        if self._shutting_down:
            return
        if not self.events.playing:
            return
        if self.playback_mode != "live":
            return
        if self._player.isPlaying():
            return
        try:
            last_error = ""
            if hasattr(self._player, "getLastError"):
                last_error = self._player.getLastError() or ""
            if last_error:
                self.log.warning(f"playback stopped unexpectedly. last player error: {last_error[:300]}")
            else:
                self.log.warning("playback stopped unexpectedly. trying forced reconnect")
            if self._refresh_playback_stream(max_retries=3, force_reconnect=True):
                self.log.info("forced reconnect succeeded")
        except Exception as e:
            self.log.error(f"forced reconnect failed: {e}")

    def player(self):
        """再生用関数"""
        self._player.setSource(self.m3u8)
        self._player.setVolume(self.volume.GetValue())
        self.log.info("playing...")
        self._player.play()

    def play(self, id, progs, program_info=None):
        """再生開始"""
        if self._shutting_down:
            return
        self._begin_playback_change()
        if self._player.isPlaying():
            try:
                self._player.stop()
            except Exception:
                pass
        self.clear_timefree_menu_live_ack()
        self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("停止"))
        self.current_station_id = id
        self.current_progs = progs
        self.events.current_playing_station_id = id
        self.playback_mode = "live"
        self._timefree_started_monotonic = None
        self._timefree_resume_position_sec = 0
        self._timefree_duration_sec = 0
        self._timefree_info = None
        self.get_streamUrl(id, progs)
        self._player.setNonSeekableInput(True)
        self._player.setHttpHeaders(self.live_http_headers)
        self._player.setStartPosition(0)
        self.player()
        self.update_program_info()
        self.events.playing = True
        self._stop_timefree_seek_timer()
        self._set_timefree_seek_ui_visible(False)
        self.streamWatchdogTimer.Start(self.stream_watchdog_interval_ms)
        self.update_timefree_command_ui()
        if hasattr(self.parent, "program_info_handler"):
            handler = self.parent.program_info_handler
            handler.clear_timefree_program_info()
            if self.events.displaying:
                if program_info:
                    handler.show_program_info_snapshot(program_info)
                else:
                    handler.get_latest_info()

        try:
            station_name = self.stid.get(id, id)
            self.parent.app.say(f"再生開始: {station_name}", interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce playback start: {e}")

    def play_timefree(self, stream_url, station_id=None, announce_text=None, headers=None, resume_seconds=0, timefree_info=None):
        """タイムフリーURLで再生開始"""
        if self._shutting_down:
            return
        self._begin_playback_change()
        if self._player.isPlaying():
            try:
                self._player.stop()
            except Exception:
                pass
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
        self.player()
        time.sleep(0.9)
        if not self._player.isPlaying():
            last_error = self._player.getLastError() or "unknown"
            self.events.playing = False
            self.playback_mode = None
            self._timefree_started_monotonic = None
            self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("再生"))
            self.update_timefree_command_ui()
            raise RuntimeError(f"タイムフリー再生の開始に失敗しました: {last_error}")
        self.events.playing = False
        self._set_timefree_seek_ui_visible(True)
        self._sync_timefree_seek_ui_from_player()
        self._start_timefree_seek_timer()
        self.update_timefree_command_ui()
        self.update_program_info()
        if hasattr(self.parent, "program_info_handler"):
            self.parent.program_info_handler.show_timefree_program_info(self._timefree_info)
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
        self.log.info("posed")
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
        was_displaying = self.events.displaying
        self.tree.DeleteAllItems()
        # 放送局リスト更新時は番組情報UIを一旦破棄し、メニューを一時的に無効にする。
        self.parent.program_info_handler.destroy_program_info_ui(disable_menu=True)
        self.creator.GetSizer().Layout()
        self.areaDetermination(progs)
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
