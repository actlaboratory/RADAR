# -*- coding: utf-8 -*-
# ラジオ局管理モジュール

import wx
import tcutil
import time
import locale
import winsound
import region_dic
import re
import lxml.etree as ET
import socket
import subprocess
import constants
import globalVars
import urllib
from simpleDialog import *
from views.mpvPlayer import MPVAudioPlayer
from views import programmanager


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
        self.tmg = tcutil.TimeManager()
        self.clutl = tcutil.CalendarUtil()
        self.stid = {}
        self.region = region_dic.REGION
        self.area = None
        self.m3u8 = None
        self.current_station_id = None
        self.current_progs = None
        self.playback_mode = None  # "live" or "timefree"
        self._last_timefree_request = None
        self._timefree_started_monotonic = None
        self._timefree_resume_position_sec = 0
        self._timefree_duration_sec = 0
        self._timefree_info = None
        self.stream_watchdog_interval_ms = 5000
        self._refresh_in_progress = False
        self.streamWatchdogTimer.Bind(wx.EVT_TIMER, self._on_stream_watchdog_timer)

    def setup_radio_ui(self):
        """ラジオ局関連のUIを設定"""
        self.volume, tmp = self.creator.slider(
            _("音量(&V)"), 
            event=self.events.onVolumeChanged, 
            defaultValue=self.app.config.getint("play", "volume", 100, 0, 100), 
            textLayout=None
        )
        self.volume.SetValue(self.app.config.getint("play", "volume"))
        
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
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                parsed_url = urllib.parse.urlparse(url)
                host = parsed_url.hostname
                port = parsed_url.port or 443
                sock.settimeout(timeout)
                
                result = sock.connect_ex((host, port))
                sock.close()
                
                if result != 0:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 2
                        self.log.debug(f"接続エラー。{wait_time}秒後にリトライします。(試行 {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    return False, "接続に失敗しました。インターネットの接続状況をご確認ください。"

                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'Mozilla/5.0')
                
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return True, response.read().decode()

            except socket.timeout:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    self.log.debug(f"タイムアウトが発生しました。{wait_time}秒後にリトライします。")
                    time.sleep(wait_time)
                else:
                    return False, "タイムアウトによりデータの取得に失敗しました。"
                
            except Exception as e:
                self.log.error(f"予期せぬエラーが発生しました: {str(e)}")
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

    def areaDetermination(self, progs):
        """エリアを判定する"""
        self.area = progs.getArea()

    def get_streamUrl(self, stationid, progs):
        """ストリームURLを取得"""
        self.m3u8 = progs.get_authenticated_stream_url(stationid)

    def _refresh_playback_stream(self, max_retries=3, force_reconnect=False):
        """再生中のストリームURLを再取得してプレイヤーへ反映"""
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

    def play(self, id, progs):
        """再生開始"""
        if self._player.isPlaying():
            try:
                self._player.stop()
            except Exception:
                pass
        self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("停止"))
        self.current_station_id = id
        self.current_progs = progs
        self.playback_mode = "live"
        self._timefree_started_monotonic = None
        self._timefree_resume_position_sec = 0
        self._timefree_duration_sec = 0
        self._timefree_info = None
        self.get_streamUrl(id, progs)
        self._player.setNonSeekableInput(True)
        self._player.setHttpHeaders(None)
        self._player.setStartPosition(0)
        self.player()
        self.update_program_info()
        self.events.playing = True
        self.streamWatchdogTimer.Start(self.stream_watchdog_interval_ms)
        self.update_timefree_command_ui()
        if hasattr(self.parent, "program_info_handler"):
            self.parent.program_info_handler.clear_timefree_program_info()

        try:
            station_name = self.stid.get(id, id)
            self.parent.app.say(f"再生開始: {station_name}", interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce playback start: {e}")

    def play_timefree(self, stream_url, station_id=None, announce_text=None, headers=None, resume_seconds=0, timefree_info=None):
        """タイムフリーURLで再生開始"""
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
        self.current_progs = None
        self.playback_mode = "timefree"
        self.m3u8 = stream_url
        resume_seconds = max(0, int(resume_seconds or 0))
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
        # radiko timefree は環境によって seekable を有効にすると無音化するため、
        # 再生安定性を優先して非シーク入力モードを維持する
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
        self.update_timefree_command_ui()
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

    def seek_timefree(self, seconds):
        """聴き逃し再生位置を移動"""
        if not self._last_timefree_request:
            raise RuntimeError("シーク可能な聴き逃し再生情報がありません。")
        target = max(0, int(seconds or 0))
        duration = self.get_timefree_duration_seconds()
        if duration > 0:
            target = min(target, duration)
        self._last_timefree_request["resume_seconds"] = target
        self._timefree_resume_position_sec = target
        self._timefree_started_monotonic = time.monotonic()
        self._replay_timefree_at(target, announce=False)

    def _replay_timefree_at(self, target_seconds, announce=False):
        """seek秒位置でURLを再生成してタイムフリー再生"""
        req = self._last_timefree_request or {}
        ft_dt = req.get("ft_dt")
        to_dt = req.get("to_dt")
        station_id = req.get("station_id")
        stream_type = req.get("stream_type", "b")

        if ft_dt and to_dt and station_id:
            pm = programmanager.ProgramManager()
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

        # 旧形式データしかない場合は保存URLで再生
        self.play_timefree(
            req.get("stream_url"),
            station_id=station_id,
            announce_text=req.get("announce_text") if announce else None,
            headers=req.get("headers"),
            resume_seconds=target_seconds,
            timefree_info=req.get("timefree_info"),
        )

    def stop_timefree(self):
        """聴き逃し再生を停止"""
        current_pos = self.get_timefree_position_seconds()
        if self._last_timefree_request is not None:
            self._last_timefree_request["resume_seconds"] = current_pos
        self._timefree_resume_position_sec = current_pos
        self._timefree_started_monotonic = None
        self._player.stop()
        self.updateInfoTimer.Stop()
        self.streamWatchdogTimer.Stop()
        self.current_station_id = None
        self.current_progs = None
        self.playback_mode = None
        self.events.playing = False
        self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("再生"))
        self.update_timefree_command_ui()
        if hasattr(self.parent, "program_info_handler"):
            self.parent.program_info_handler.clear_timefree_program_info()
        try:
            self.parent.app.say("聴き逃し再生停止", interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce timefree playback stop: {e}")

    def stop(self):
        """再生停止"""
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
        self.update_timefree_command_ui()
        if hasattr(self.parent, "program_info_handler"):
            self.parent.program_info_handler.clear_timefree_program_info()

        try:
            self.parent.app.say("再生停止", interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce playback stop: {e}")

    def update_program_info(self):
        """番組情報更新タイマーを開始"""
        self.updateInfoTimer.Start(self.tmg.replace_milliseconds(3))  # 設定した頻度で番組情報を更新
        self.updateInfoTimer.Bind(wx.EVT_TIMER, self.events.onUpdateProcess)

    def get_latest_programList(self, progs):
        """番組リストを最新に更新"""
        self.tree.DeleteAllItems()
        # 番組情報が表示されている場合のみクリア
        if self.events.displaying:
            self.parent.program_info_handler.nplist.clear()
            self.parent.program_info_handler.DSCBOX.Disable()
        self.areaDetermination(progs)
        self.setupradio()
        self.setRadioList()

    def exit(self):
        """終了処理"""
        self.streamWatchdogTimer.Stop()
        self.updateInfoTimer.Stop()
        self._player.exit()

    def is_live_playing(self):
        """ライブ再生中かどうか"""
        return self.playback_mode == "live" and self.events.playing

    def update_timefree_command_ui(self):
        """聴き逃し再生コマンドの有効状態とラベルを更新"""
        if not hasattr(self.parent, "menu"):
            return
        try:
            is_timefree = self.is_timefree_playing()
            is_live = self.is_live_playing()
            label = _("聴き逃し停止") if is_timefree else _("聴き逃し再生")
            self.parent.menu.SetMenuLabel("FUNCTION_TIMEFREE_TOGGLE", label)
            # ライブ再生中(F1で放送中番組再生中)は無効
            self.parent.menu.EnableMenu("FUNCTION_TIMEFREE_TOGGLE", not is_live)
        except Exception as e:
            self.log.error(f"Failed to update timefree command UI: {e}")
