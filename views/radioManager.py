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
from soundPlayer import player
from soundPlayer.constants import *


class RadioManager:
    def __init__(self, parent_view):
        self.parent = parent_view
        self.log = parent_view.log
        self.app = parent_view.app
        self.creator = parent_view.creator
        self.events = parent_view.events
        
        # ラジオ局関連の初期化
        self._player = player.player()
        self.updateInfoTimer = wx.Timer()
        self.tmg = tcutil.TimeManager()
        self.clutl = tcutil.CalendarUtil()
        self.stid = {}
        self.region = region_dic.REGION
        self.area = None
        self.m3u8 = None

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
        # ラジオ局情報の取得
        url = "https://radiko.jp/v3/station/region/full.xml"
        success, result = self.get_radio_stations(url)
        if not success:
            errorDialog(_(result))
            self.tree.SetFocus()
            self.tree.Expand(root)
            self.tree.SelectItem(root, select=True)
            return

        try:
            # XMLのパース
            parsed = ET.fromstring(result.encode('utf-8'))
            
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
        url = f'http://f-radiko.smartstream.ne.jp/{stationid}/_definst_/simul-stream.stream/playlist.m3u8'
        self.m3u8 = progs.gettoken.gen_temp_chunk_m3u8_url(url, progs.token)

    def player(self):
        """再生用関数"""
        self._player.setSource(self.m3u8)
        self._player.setVolume(self.volume.GetValue())
        self.log.info("playing...")
        self._player.play()

    def play(self, id, progs):
        """再生開始"""
        self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("停止"))
        self.get_streamUrl(id, progs)
        self.player()
        self.update_program_info()
        self.events.playing = True
        
        # スクリーンリーダーで再生開始を通知
        try:
            station_name = self.stid.get(id, id)
            self.parent.app.say(f"再生開始: {station_name}", interrupt=True)
        except Exception as e:
            self.log.error(f"Failed to announce playback start: {e}")

    def stop(self):
        """再生停止"""
        self._player.stop()
        self.parent.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("再生"))
        self.log.info("posed")
        self.updateInfoTimer.Stop()
        self.log.debug("timer is stoped!")
        self.events.playing = False
        
        # スクリーンリーダーで再生停止を通知
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
        self.tree.Destroy()
        # 番組情報が表示されている場合のみクリア
        if self.events.displaying:
            self.parent.program_info_handler.nplist.clear()
            self.parent.program_info_handler.DSCBOX.Disable()
        self.areaDetermination(progs)
        self.AreaTreeCtrl()
        self.setupradio()
        self.setRadioList()

    def exit(self):
        """終了処理"""
        self._player.exit()
