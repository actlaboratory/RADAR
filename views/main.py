# -*- coding: utf-8 -*-
# main view
# Copyright (C) 2019 Yukio Nozawa <personal@nyanchangames.com>
# Copyright (C) 2019-2021 yamahubuki <itiro.ishino@gmail.com>

import wx
import tcutil
import time
import locale

# ロケール設定を修正
try:
    locale.setlocale(locale.LC_TIME, 'Japanese_Japan.932')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'ja_JP.UTF-8')
    except locale.Error:
        try:
            locale.setlocale(locale.LC_TIME, 'C')
        except locale.Error:
            pass  # デフォルトのまま
import winsound
import region_dic
import re
from views import showRadioProgramScheduleListBase
from plyer import notification
import recorder
from views import recordingWizzard
from views import token
from views import programmanager
from views import changeDevice
import xml.etree.ElementTree as ET
import socket
import subprocess
import constants
import globalVars
import update
import menuItemsStore
import datetime
from .base import *
import urllib
from simpleDialog import *

from views import globalKeyConfig
from views import sample
from views import settingsDialog
from views import versionDialog
from soundPlayer import player
from soundPlayer.constants import *


class MainView(BaseView):
	def __init__(self):
		super().__init__("mainView")
		self.log.debug("created")
		self.events = Events(self, self.identifier)
		title = constants.APP_NAME
		super().Initialize(
			title,
			self.app.config.getint(self.identifier, "sizeX", 800, 400),
			self.app.config.getint(self.identifier, "sizeY", 600, 300),
			self.app.config.getint(self.identifier, "positionX", 50, 0),
			self.app.config.getint(self.identifier, "positionY", 50, 0)
		)
		self.InstallMenuEvent(Menu(self.identifier), self.events.OnMenuSelect)

		self._player = player.player()
		self.updateInfoTimer = wx.Timer()
		self.recordingStatusTimer = wx.Timer()
		self.tmg = tcutil.TimeManager()
		self.clutl = tcutil.CalendarUtil()
		self.progs = programmanager.ProgramManager()
		# レコーダーは新しいシステムを使用（recorder_manager）
		# ファイルタイプは設定から取得
		self.areaDetermination()
		self.description()
		self.volume, tmp = self.creator.slider(_("音量(&V)"), event=self.events.onVolumeChanged, defaultValue=self.app.config.getint("play", "volume", 100, 0, 100), textLayout=None)
		self.volume.SetValue(self.app.config.getint("play", "volume"))
		self.exit_button()
		self.SHOW_NOW_PROGRAMLIST()
		self.AreaTreeCtrl()
		self.setupradio()
		self.setRadioList()
		# 録音設定の初期化
		try:
			menu_id = self.app.config.getint("recording", "menu_id")
			check_menu = self.app.config.getboolean("recording", "check_menu")
		except:
			# 設定が存在しない場合はデフォルト値を使用
			menu_id = 10000  # MP3
			check_menu = True
			# 設定を保存
			self.app.config["recording"]["menu_id"] = menu_id
			self.app.config["recording"]["check_menu"] = check_menu
		
		self.menu.hRecordingFileTypeMenu.Check(menu_id, check_menu)
		self.menu.hMenuBar.Enable(menuItemsStore.getRef("HIDE_PROGRAMINFO"),False)
		self.events._update_schedule_menu_status()
		
		# 録音スケジュール監視を開始
		try:
			from recorder import schedule_manager
			schedule_manager.start_monitoring()
			self.log.info("Recording schedule monitoring started")
		except Exception as e:
			self.log.error(f"Failed to start schedule monitoring: {e}")
		
		# 録音状態監視タイマーを開始
		self.recordingStatusTimer.Start(5000)  # 5秒ごとにチェック
		self.recordingStatusTimer.Bind(wx.EVT_TIMER, self.events.check_recording_status)

	def update_program_info(self):
		self.updateInfoTimer.Start(self.tmg.replace_milliseconds(3)) #設定した頻度で番組情報を更新
		self.updateInfoTimer.Bind(wx.EVT_TIMER, self.events.onUpdateProcess)

	def SHOW_NOW_PROGRAMLIST(self):
		self.nplist,nowprograminfo = self.creator.virtualListCtrl(_("現在再生中の番組"))
		self.nplist.AppendColumn(_("現在再生中"))
		self.nplist.AppendColumn(_(""))
		self.nplist.Disable()

	def description(self):
		#番組の説明の表示部分をつくる
		self.DSCBOX, label = self.creator.inputbox(_("説明"), style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_PROCESS_ENTER) #読み取り専用のテキストボックス
		self.DSCBOX.Disable() #初期状態は無効

	def AreaTreeCtrl(self):
		self.tree,broadcaster = self.creator.treeCtrl(_("放送エリア"))


	def backbtn(self):
		self.bkbtn = self.creator.cancelbutton(_("前の画面に戻る"), None)

	def nextbtn(self):
		self.nxtBtn = self.creator.button(_("次へ&(N)", None))

	def setupradio(self):
		"""ステーションidを取得後、ツリービューに描画"""
		self.stid = {}
		self.region = region_dic.REGION
		if self.area in self.region:
			self.log.debug("region:"+self.region[self.area])
		#ツリーのルート項目の作成
		root = self.tree.AddRoot(_("放送局一覧"))
		#エリア情報の取得に失敗
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
			parsed = ET.fromstring(result)
			
			for r in parsed:
				for station in r:
					stream = {r.attrib["ascii_name"]:{}}
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
			errorDialog(_("予期せぬエラーが発生しました。\n詳細はログをご確認ください。この問題が引き続き発生する場合は開発者までお問い合わせください。"))
			return

		# イベントバインドとツリーの設定
		self.tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self.events.onRadioActivated)
		self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.events.onRadioSelected)
		self.tree.SetFocus()
		self.tree.Expand(root)
		self.tree.SelectItem(root, select=True)

	def areaDetermination(self):
		"""エリアを判定する"""
		self.area = self.progs.getArea()

	def get_streamUrl(self, stationid):
		url = f'http://f-radiko.smartstream.ne.jp/{stationid}/_definst_/simul-stream.stream/playlist.m3u8'
		self.m3u8 = self.progs.gettoken.gen_temp_chunk_m3u8_url( url ,self.progs.token)

	def player(self):
		"""再生用関数"""
		self._player.setSource(self.m3u8)
		self._player.setVolume(self.volume.GetValue())
		self.log.info("playing...")
		self._player.play()

	def exit_button(self):
		self.exitbtn = self.creator.button(_("終了"), self.events.exit)

	def play(self, id):
		self.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("停止"))
		self.get_streamUrl(id)
		self.player()
		self.update_program_info()
		self.events.playing = True

	def stop(self):
		self._player.stop()
		self.menu.SetMenuLabel("FUNCTION_PLAY_PLAY", _("再生"))
		self.log.info("posed")
		self.updateInfoTimer.Stop()
		self.log.debug("timer is stoped!")
		self.events.playing = False

	def get_latest_info(self):
		"""ctrl+f5によるリロード処理のときに呼ばれる"""
		self.nplist.clear()
		self.events.show_program_info()
		self.events.show_onair_music()
		self.events.show_description()

	def get_latest_programList(self):
		"""f5押したら呼ばれる"""
		self.tree.Destroy()
		self.nplist.clear()
		self.DSCBOX.Disable()
		self.areaDetermination()
		self.AreaTreeCtrl()
		self.setupradio()
		self.setRadioList()

class Menu(BaseMenu):
	def Apply(self, target):
		"""指定されたウィンドウに、メニューを適用する。"""

		# メニュー内容をいったんクリア
		self.hMenuBar = wx.MenuBar()

		# メニューの大項目を作る
		self.hFileMenu = wx.Menu()
		self.hFunctionMenu = wx.Menu()
		self.hRecordingMenu = wx.Menu()
		self.hRecordingFileTypeMenu = wx.Menu()
		self.hRecordingFileTypeMenu.Bind(wx.EVT_MENU, self.parent.events.onRecordMenuSelect)
		self.hProgramListMenu = wx.Menu()
		self.hOptionMenu = wx.Menu()
		self.hHelpMenu = wx.Menu()

		# ファイルメニュー
		self.RegisterMenuCommand(self.hFileMenu, {
			"FILE_EXAMPLE": self.parent.events.example,
			"FILE_RELOAD":self.parent.events.onReLoad,
			"FILE_EXIT": self.parent.events.exit,
		})

		#機能メニュー
		self.RegisterMenuCommand(self.hFunctionMenu, {
			"FUNCTION_PLAY_PLAY":self.parent.events.onRadioActivated,
			"FUNCTION_VOLUME_UP":self.parent.events.volume_up,
			"FUNCTION_VOLUME_DOWN":self.parent.events.volume_down,
			"FUNCTION_PLAY_MUTE":self.parent.events.onMute,
			"FUNCTION_OUTPUT_CHANGEDEVICE":self.parent.events.changeOutputDevice,
		})

		#番組メニュー
		self.RegisterMenuCommand(self.hProgramListMenu, {
			"SHOW_PROGRAMLIST":self.parent.events.initializeInfoView,
			"HIDE_PROGRAMINFO":self.parent.events.switching_programInfo,
			"UPDATE_PROGRAMLIST":self.parent.events.onUpdateProgram,
		})

		#録音メニュー
		self.RegisterMenuCommand(self.hRecordingMenu, {
			"RECORDING_IMMEDIATELY":self.parent.events.record_immediately,
			"RECORDING_SCHEDULE":self.parent.events.recording_schedule,
			"RECORDING_SCHEDULE_MANAGE":self.parent.events.manage_schedules,
			"RECORDING_MANAGE":self.parent.events.manage_recordings,
		})

		#録音品質選択メニュー
		self.RegisterMenuCommand(self.hRecordingMenu, "RECORDING_OPTION", subMenu=self.hRecordingFileTypeMenu)
		#録音品質選択メニューの中身
		self.hRecordingFileTypeMenu.AppendCheckItem(constants.RECORDING_MP3, "mp3")
		self.hRecordingFileTypeMenu.AppendCheckItem(constants.RECORDING_WAV, "wav")

		# オプションメニュー
		self.RegisterMenuCommand(self.hOptionMenu, {
			"OPTION_OPTION": self.parent.events.option,
			"OPTION_KEY_CONFIG": self.parent.events.keyConfig,
		})

		# ヘルプメニュー
		self.RegisterMenuCommand(self.hHelpMenu, {
			"HELP_UPDATE": self.parent.events.checkUpdate,
			"HELP_VERSIONINFO": self.parent.events.version,
		})

		# メニューバーの生成
		self.hMenuBar.Append(self.hFileMenu, _("ファイル(&F))"))
		self.hMenuBar.Append(self.hFunctionMenu, _("機能(&F)"))
		self.hMenuBar.Append(self.hProgramListMenu, _("番組(&p)"))
		self.hMenuBar.Append(self.hRecordingMenu, _("録音(&r)"))
		self.hMenuBar.Append(self.hOptionMenu, _("オプション(&O)"))
		self.hMenuBar.Append(self.hHelpMenu, _("ヘルプ(&H)"))
		target.SetMenuBar(self.hMenuBar)


class Events(BaseEvents):
	playing = False
	mute_status = False
	displaying = True #番組情報表示中
	id = None
	recording = False


	def onRecordMenuSelect(self, event):
		"""録音品質メニューの動作"""
		selected = event.GetId()
		
		# 排他的選択（ラジオボタン的な動作）
		if selected == 10000 and self.parent.menu.hRecordingFileTypeMenu.IsChecked(selected):
			self.parent.menu.hRecordingFileTypeMenu.Check(10001, False)  # WAVをオフ
		elif selected == 10001 and self.parent.menu.hRecordingFileTypeMenu.IsChecked(selected):
			self.parent.menu.hRecordingFileTypeMenu.Check(10000, False)  # MP3をオフ
		
		# 設定を保存
		self.parent.app.config["recording"]["menu_id"] = selected
		self.parent.app.config["recording"]["check_menu"] = self.parent.menu.hRecordingFileTypeMenu.IsChecked(selected)
		
		# デバッグログ
		filetype = "mp3" if selected == 10000 else "wav"
		self.log.info(f"Recording file type changed to: {filetype}")

	def onUpdateProcess(self, event):
		self.parent.get_latest_info()

	def example(self, event):
		d = sample.Dialog()
		d.Initialize()
		r = d.Show()

	def exit(self, event):
		try:
			# 録音状態監視タイマーを停止
			if self.parent.recordingStatusTimer:
				self.parent.recordingStatusTimer.Stop()
			
			# 録音スケジュールのクリーンアップ
			from recorder import schedule_manager
			schedule_manager.cleanup()
			
			# 全ての録音を停止
			from recorder import recorder_manager
			recorder_manager.cleanup()
			
			self.log.info("Application cleanup completed")
		except Exception as e:
			self.log.error(f"Error during application cleanup: {e}")
		
		self.parent._player.exit()
		self.parent.hFrame.Close()

	def option(self, event):
		d = settingsDialog.Dialog()
		d.Initialize()
		d.Show()

	def keyConfig(self, event):
		if self.setKeymap(self.parent.identifier, _("ショートカットキーの設定"), filter=keymap.KeyFilter().SetDefault(False, False)):
			# ショートカットキーの変更適用とメニューバーの再描画
			self.parent.menu.InitShortcut()
			self.parent.menu.ApplyShortcut(self.parent.hFrame)
			self.parent.menu.Apply(self.parent.hFrame)

	def checkUpdate(self, event):
		update.checkUpdate()

	def version(self, event):
		d = versionDialog.dialog()
		d.Initialize()
		r = d.Show()

	def setKeymap(self, identifier, ttl, keymap=None, filter=None):
		if keymap:
			try:
				keys = keymap.map[identifier.upper()]
			except KeyError:
				keys = {}
		else:
			try:
				keys = self.parent.menu.keymap.map[identifier.upper()]
			except KeyError:
				keys = {}
		keyData = {}
		menuData = {}
		for refName in defaultKeymap.defaultKeymap[identifier].keys():
			title = menuItemsDic.getValueString(refName)
			if refName in keys:
				keyData[title] = keys[refName]
			else:
				keyData[title] = _("なし")
			menuData[title] = refName

		d = globalKeyConfig.Dialog(keyData, menuData, [], filter)
		d.Initialize(ttl)
		if d.Show() == wx.ID_CANCEL:
			return False

		keyData, menuData = d.GetValue()

		# キーマップの既存設定を置き換える
		newMap = ConfigManager.ConfigManager()
		newMap.read(constants.KEYMAP_FILE_NAME)
		for name, key in keyData.items():
			if key != _("なし"):
				newMap[identifier.upper()][menuData[name]] = key
			else:
				newMap[identifier.upper()][menuData[name]] = ""
		newMap.write()
		return True

	def onRadioActivated(self, event):
		self.id = self.parent.tree.GetItemData(self.parent.tree.GetFocusedItem()) #stationIDが出る
		if self.id == None:
			return
		self.parent.log.info("activated" + self.id)
		try:
			if not self.playing:
				self.parent.play(self.id)

			else:
				self.parent.stop()
		except urllib.request.HTTPError as error:
			errorDialog(_("再生に失敗しました。聴取可能な都道府県内であることをご確認ください。\nこの症状が引き続き発生する場合は、放送局一覧を再描画してからお試しください。"))
			self.parent.log.error("Playback failure!"+str(error))
			return

		self.parent.nplist.Enable()
		self.parent.nplist.clear()
		self.show_program_info()
		self.show_onair_music()

#メニュー項目の表示
		self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("HIDE_PROGRAMINFO"), True)

		#番組の説明
		self.show_description()

	def show_description(self):
		"""番組の説明を表示"""
		if self.parent.progs.getNowProgramDsc(self.id):
			self.parent.DSCBOX.Enable()
			self.parent.DSCBOX.SetValue(self.parent.progs.getNowProgramDsc(self.id))
		else:
			self.parent.DSCBOX.SetValue("説明無し")

	def show_program_info(self):
		program_title = self.parent.progs.getNowProgram(self.id)
		self.program_title = program_title
		program_pfm = self.parent.progs.getnowProgramPfm(self.id)
		if self.id in self.parent.stid:
			result = self.parent.stid[self.id]
			self.result = result

		#リストビューにアペンド
		self.parent.nplist.Append(("放送局", self.result), )
		self.parent.nplist.Append(("番組名", program_title), )
		self.parent.nplist.Append(("出演者", program_pfm), )

	def show_onair_music(self):
		#オンエア曲情報を取得してくる
		try:
			onair_music = self.parent.progs.get_onair_music(self.id)
		except OSError:
			onair_music = None

#リストビューにアペンド
		self.parent.nplist.Append(("オンエア曲", onair_music, ), )

	def onRadioSelected(self, event):
		self.selected = self.parent.tree.GetItemData(self.parent.tree.GetFocusedItem())
		if self.selected == None:
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_PROGRAMLIST"),False)
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("RECORDING_IMMEDIATELY"),False)
			return
		
		self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_PROGRAMLIST"),True)
		
		# 選択した放送局の録音状態をチェックしてメニューを更新
		self._update_recording_menu_for_station(self.selected)

	def onVolumeChanged(self, event):
		value = self.parent.volume.GetValue()
		self.parent._player.setVolume(value)
		self.parent.app.config["play"]["volume"] = value

	def volume_up(self, event):
		value = self.parent.volume.GetValue()
		if value == self.parent.volume.GetMax():
			return
		self.parent.volume.SetValue(value+10) #ボリュームを10％上げる
		self.onVolumeChanged(event)
		self.parent.log.debug("volume increased")

	def volume_down(self, event):
		value = self.parent.volume.GetValue()
		if value == self.parent.volume.GetMin():
			return
		self.parent.volume.SetValue(value-10) #ボリュームを10％下げる
		self.onVolumeChanged(event)
		self.parent.log.debug("volume decreased")

	def initializeInfoView(self, event):
		proglst = showRadioProgramScheduleListBase.ShowSchedule(self.selected, self.parent.stid[self.selected])
		proglst.Initialize()
		proglst.Show()
		return

	def onMute(self, event):
		if not self.mute_status:
			self.parent.menu.SetMenuLabel("FUNCTION_PLAY_MUTE", _("ミュートを解除"))
			self.parent._player.setVolume(0)
			self.parent.volume.Disable()
			self.mute_status = True
		else:
			self.parent.menu.SetMenuLabel("FUNCTION_PLAY_MUTE", _("ミュート"))
			self.parent._player.setVolume(self.parent.volume.GetValue())
			self.parent.volume.Enable()
			self.mute_status = False

	def switching_programInfo(self, event):
		if self.displaying:
			self.parent.menu.SetMenuLabel("HIDE_PROGRAMINFO", _("番組情報を表示&P"))
			self.parent.nplist.Disable()
			self.displaying = False
		else:
			self.parent.menu.SetMenuLabel("HIDE_PROGRAMINFO", _("番組情報の非表示&H"))
			self.parent.nplist.Enable()
			self.displaying = True

	def changeOutputDevice(self, event):
		changeDeviceDialog = changeDevice.ChangeDeviceDialog()
		changeDeviceDialog.Initialize()
		ret = changeDeviceDialog.Show()
		if ret==wx.ID_CANCEL: return
		self.parent._player.setDeviceByName(changeDeviceDialog.GetData())

	def onReLoad(self, event):
		"""リロードを処理する"""
		self.parent.get_latest_info()


	def onUpdateProgram(self, event):
		"""最新の番組一覧に更新"""
		self.parent.stop()
		self.parent.get_latest_programList()

	def record_immediately(self, event):
		"""録音の開始/停止を処理するメソッド"""
		if self.selected is None:
			self.log.debug("No station selected for recording")
			return

		# 選択した放送局の録音状態をチェック
		from recorder import recorder_manager
		is_station_recording = self._is_station_recording(self.selected)
		
		# 録音中の場合は停止処理
		if is_station_recording:
			self._stop_station_recording(self.selected)
			return

		# 録音開始処理
		try:
			# 現在の番組情報を取得
			title = self.parent.progs.getNowProgram(self.selected)
			if not title:
				self.log.warning("Failed to get program title, using fallback")
				# 番組タイトルが取得できない場合は、放送局名と時刻を使用
				current_time = datetime.datetime.now().strftime("%H%M")
				title = f"番組不明_{current_time}"
			else:
				# 番組タイトルをファイル名に適した形式に変換
				title = re.sub(r'[<>:"/\\|?*]', '_', title)  # 無効な文字を置換
				title = title.strip()

			# ストリームURLの取得
			self.parent.get_streamUrl(self.selected)
			if not self.parent.m3u8:
				self.log.error("Failed to get stream URL")
				errorDialog(_("ストリームURLの取得に失敗しました。"))
				return

			# ファイル名とディレクトリの準備
			replace = title.replace(" ", "-")
			station_dir = self.parent.stid[self.selected].replace(" ", "_")
			from recorder import create_recording_dir
			dirs = create_recording_dir(station_dir, title)
			file_path = f"{dirs}\{str(datetime.date.today())}_{replace}"
			
			# ファイルタイプを取得（現在のメニュー選択状態から）
			if self.parent.menu.hRecordingFileTypeMenu.IsChecked(10001):  # WAV
				filetype = "wav"
			else:  # MP3（デフォルト）
				filetype = "mp3"
			self.log.info(f"Recording with file type: {filetype}")
			
			stream_url = self.parent.m3u8
			end_time = time.time() + (8 * 3600)  # 8時間後
			info = f"{self.parent.stid[self.selected]} {title}"
			
			# 録音完了時のコールバック
			def on_recording_complete(recorder):
				self._update_recording_menu_for_station(self.selected)
				notification.notify(
					title='録音完了',
					message=f'{title} の録音が完了しました。',
					app_name='rpb',
					timeout=10
				)
			
			recorder = recorder_manager.start_recording(stream_url, file_path, info, end_time, filetype, on_recording_complete)
			if recorder:
				self.log.info(f"Recording started: {title}")
				self._update_recording_menu_for_station(self.selected)
				# 録音開始時の通知
				notification.notify(
					title='録音開始',
					message=f'{title} の録音を開始しました。',
					app_name='rpb',
					timeout=10
				)
			else:
				self.log.error("Recording failed to start")
				errorDialog(_("録音の開始に失敗しました。"))
		
		except Exception as e:
			self.log.error(f"Error during recording start: {e}")
			errorDialog(_("録音の開始中にエラーが発生しました。"))
			self._update_recording_menu_for_station(self.selected)


	def check_recording_status(self, event):
		"""録音状態をチェックしてUIを更新"""
		try:
			# 現在選択されている放送局がある場合、その放送局の録音状態をチェック
			if self.selected:
				self._update_recording_menu_for_station(self.selected)
			
			# 予約録音の状態をチェックしてメニュー項目を更新
			self._update_schedule_menu_status()
					
		except Exception as e:
			self.log.error(f"Error checking recording status: {e}")

	def _update_schedule_menu_status(self):
		"""予約録音の状態に応じてメニュー項目を更新"""
		try:
			# 予約録音管理メニューは常に有効
			pass
		except Exception as e:
			self.log.error(f"Error updating schedule menu status: {e}")

	def _is_station_recording(self, station_id):
		"""指定された放送局が録音中かどうかを判定"""
		try:
			from recorder import recorder_manager
			return recorder_manager.is_station_recording(station_id)
		except Exception as e:
			self.log.error(f"Error checking station recording status: {e}")
			return False

	def _stop_station_recording(self, station_id):
		"""指定された放送局の録音を停止"""
		try:
			from recorder import recorder_manager
			stopped_count = recorder_manager.stop_station_recording(station_id)
			
			if stopped_count > 0:
				self.log.info(f"Stopped {stopped_count} recording(s) for station: {station_id}")
				notification.notify(
					title='録音停止',
					message=f'{self.parent.stid.get(station_id, station_id)} の録音を停止しました。',
					app_name='rpb',
					timeout=10
				)
			else:
				self.log.warning(f"No active recordings found for station: {station_id}")
			
			# メニューを更新（件数表示のみ）
			self._update_recording_menu_for_station(station_id)
			
		except Exception as e:
			self.log.error(f"Error stopping station recording: {e}")
			errorDialog(_("録音の停止中にエラーが発生しました。"))

	def _update_recording_menu_for_station(self, station_id):
		"""指定された放送局の録音状態に応じてメニューを更新（件数表示のみ）"""
		try:
			from recorder import recorder_manager
			active_count = len(recorder_manager.get_active_recorders())
			
			# 件数表示のみでメニューラベルを更新
			if active_count > 0:
				self.parent.menu.SetMenuLabel("RECORDING_IMMEDIATELY", _("今すぐ録音(&R)") + f" ({active_count}件録音中)")
			else:
				self.parent.menu.SetMenuLabel("RECORDING_IMMEDIATELY", _("今すぐ録音(&R)"))
			
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("RECORDING_IMMEDIATELY"), True)
			
		except Exception as e:
			self.log.error(f"Error updating recording menu for station: {e}")

	def recording_schedule(self, event):
		"""録音予約ウィザードを表示"""
		try:
			# 常に新規作成ウィザードを表示
			rw = recordingWizzard.RecordingWizzard(self.selected, self.parent.stid[self.selected])
			rw.Show()
				
		except Exception as e:
			self.log.error(f"Error in recording schedule: {e}")
			errorDialog(_("録音予約の処理に失敗しました。"))


	def manage_schedules(self, event):
		"""予約録音管理ダイアログを表示"""
		try:
			from views import scheduledRecordingManager
			dialog = scheduledRecordingManager.ScheduledRecordingManager()
			dialog.Initialize()
			dialog.Show()
			
		except Exception as e:
			self.log.error(f"Error in manage_schedules: {e}")
			errorDialog(f"予約録音管理の表示に失敗しました: {e}")

	def manage_recordings(self, event):
		"""録音管理ダイアログを表示"""
		try:
			from views import recordingManager
			dialog = recordingManager.RecordingManagerDialog()
			dialog.Initialize()
			dialog.Show()
			
		except Exception as e:
			self.log.error(f"Error in manage_recordings: {e}")
			errorDialog(f"録音管理の表示に失敗しました: {e}")