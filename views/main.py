# -*- coding: utf-8 -*-
# main view
# Copyright (C) 2019 Yukio Nozawa <personal@nyanchangames.com>
# Copyright (C) 2019-2021 yamahubuki <itiro.ishino@gmail.com>

import wx
import tcutil
import time
import winsound
import region_dic
import re
import recorder
from views import token
from views import programmanager
from views import changeDevice
import xml.etree.ElementTree as ET
import itertools
import subprocess

import constants
import globalVars
import update
import menuItemsStore
import datetime

from .base import *
from urllib import request
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
		self.timer = wx.Timer()
		self.tmg = tcutil.TimeManager()
		self.clutl = tcutil.CalendarUtil()
		self.progs = programmanager.ProgramManager()
		self.recorder = recorder.Recorder() #recording moduleをインスタンス化
		try:
			self.menu.hRecordingFileTypeMenu.Check(self.app.config.getint("recording","menu_id"), self.app.config.getboolean("recording","check_menu"))
			self.recorder.setFileType(self.app.config.getint("recording", "menu_id")-10000)
		except:
			pass

		self.areaDetermination()
		self.description()
		self.volume, tmp = self.creator.slider(_("音量(&V)"), event=self.events.onVolumeChanged, defaultValue=self.app.config.getint("play", "volume", 100, 0, 100), textLayout=None)
		self.volume.SetValue(self.app.config.getint("play", "volume"))
		self.exit_button()
		self.SHOW_NOW_PROGRAMLIST()
		self.AreaTreeCtrl()
		self.getradio()
		self.calendar()
		self.menu.hMenuBar.Enable(menuItemsStore.getRef("HIDE_PROGRAMINFO"),False)
		self.menu.hMenuBar.Enable(menuItemsStore.getRef("RECORDING_IMMEDIATELY"),False)

	def update_program_info(self):
		value = self.app.config.getint("general", "frequency")
		self.timer.Start(self.tmg.replace_milliseconds(value)) #設定した頻度で番組情報を更新
		self.timer.Bind(wx.EVT_TIMER, self.events.onUpdateProcess)

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

	def infoListView(self):
		self.lst,programinfo = self.creator.virtualListCtrl(_("番組表一覧"))
		self.lst.AppendColumn(_("タイトル"))
		self.lst.AppendColumn(_("出演者"))
		self.lst.AppendColumn(_("開始時間"))
		self.lst.AppendColumn(_("終了時間"))
		self.backbtn()
		self.calendarSelector()

	def calendarSelector(self):
		"""日時指定用コンボボックスを作成し、内容を設定"""
		self.result = []
		year = self.clutl.year
		month = self.clutl.month
		day = datetime.datetime.now().day
		for cal in self.calendar_lists[day:day+7]:
			if len(str(cal)) < 2:
				self.result.append(f"{year}/{month}/0{cal}")
			else:
				self.result.append(f"{year}/{month}/{cal}")
		self.cmb,label = self.creator.combobox(_("日時を指定"), self.result)
		self.cmb.Bind(wx.EVT_COMBOBOX, self.events.show_week_programlist)

	def backbtn(self):
		self.bkbtn = self.creator.button(_("前の画面に戻る"), self.events.onbackbutton)
		return

	def getradio(self):
		"""ステーションidを取得後、ツリービューに描画"""
		self.stid = {}
		region = region_dic.REGION
		if self.area in region:
			self.log.debug("region:"+region[self.area])
		#ツリーのルート項目の作成
		root = self.tree.AddRoot(_("放送局一覧"))
		#エリア情報の取得に失敗
		if not self.area:
			errorDialog(_("エリア情報の取得に失敗しました。\nインターネットの接続状況をご確認ください"))
			self.tree.SetFocus()
			self.tree.Expand(root)
			self.tree.SelectItem(root, select=True)
			return

		#ラジオ番組の取得
		url = "https://radiko.jp/v3/station/region/full.xml" #放送局リストurl
		#xmlから情報取得
		req = request.Request(url) 
		with request.urlopen(req) as response:
			xml_data = response.read().decode() #デフォルトではbytesオブジェクトなので文字列へのデコードが必要
			parsed = ET.fromstring(xml_data)
			for r in parsed:
				for station in r:
					stream = {r.attrib["ascii_name"]:{}}
					stream[r.attrib["ascii_name"]] = {"radioname":station.find("name").text,"radioid":station.find("id").text}
					if "ZENKOKU" in stream:
						self.tree.AppendItem(root, stream["ZENKOKU"]["radioname"], data=stream["ZENKOKU"]["radioid"])
					if region[self.area] in stream:
						self.tree.AppendItem(root, stream[region[self.area]]["radioname"], data=stream[region[self.area]]["radioid"])
						self.stid[stream[region[self.area]]["radioid"]] = stream[region[self.area]]["radioname"]
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

	def calendar(self):
		self.calendar_lists = list(itertools.chain.from_iterable(self.clutl.getMonth())) #２次元リストを一次元に変換

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
		self.timer.Stop()
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
		self.getradio()


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
			"SHOW_NOW_PROGRAMLIST":self.parent.events.nowProgramInfo,
			"SHOW_WEEK_PROGRAMLIST":self.parent.events.weekProgramInfo,
			"HIDE_PROGRAMINFO":self.parent.events.switching_programInfo,
			"UPDATE_PROGRAMLIST":self.parent.events.onUpdateProgram,
		})

		#録音メニュー
		self.RegisterMenuCommand(self.hRecordingMenu, {
			"RECORDING_IMMEDIATELY":self.parent.events.record_immediately,
			"RECORDING_SCHEDULE":None,
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
		if selected == 10000 and self.parent.menu.hRecordingFileTypeMenu.IsChecked(selected):
			self.parent.menu.hRecordingFileTypeMenu.Check(selected+1, False)
		if selected == 10001 and self.parent.menu.hRecordingFileTypeMenu.IsChecked(selected):
			self.parent.menu.hRecordingFileTypeMenu.Check(selected-1, False)
		self.parent.recorder.setFileType(selected - 10000)
		self.parent.app.config["recording"]["menu_id"] = selected
		self.parent.app.config["recording"]["check_menu"] = self.parent.menu.hRecordingFileTypeMenu.IsChecked(selected)

	def onUpdateProcess(self, event):
		self.parent.get_latest_info()

	def example(self, event):
		d = sample.Dialog()
		d.Initialize()
		r = d.Show()

	def exit(self, event):
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
		self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("RECORDING_IMMEDIATELY"), True)
		if self.id == None:
			return
		self.parent.log.info("activated" + self.id)
		try:
			if not self.playing:
				self.parent.play(self.id)

			else:
				self.parent.stop()
		except request.HTTPError as error:
			errorDialog(_("再生に失敗しました。聴取可能な都道府県内であることをご確認ください。\nこの症状が引き続き発生する場合は、番組一覧の更新を行ってください。"))
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
		if self.parent.progs.getProgramDsc(self.id):
			self.parent.DSCBOX.Enable()
			self.parent.DSCBOX.SetValue(self.parent.progs.getProgramDsc(self.id))
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
		self.parent.nplist.Append(("放送局", result), )
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
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_NOW_PROGRAMLIST"),False)
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_WEEK_PROGRAMLIST"),False)
			return
		self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_NOW_PROGRAMLIST"), True)
		self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_WEEK_PROGRAMLIST"),True)

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

	def nowProgramInfo(self, event):
		self.parent.progs.retrieveRadioListings(self.selected)
		title = self.parent.progs.gettitle() #番組のタイトル
		pfm = self.parent.progs.getpfm() #出演者の名前
		program_ftl = self.parent.progs.get_ftl() #番組開始時間
		program_tol = self.parent.progs.get_tol() #番組終了時間
		self.parent.Clear()
		self.parent.infoListView()
		self.parent.cmb.Destroy()
		for t,p,ftl,tol in zip(title,pfm,program_ftl,program_tol):
			self.parent.lst.Append((t,p, ftl[:2]+":"+ftl[2:4],tol[:2]+":"+tol[2:4]), )
		self.parent.lst.SetFocus()

	def weekProgramInfo(self, event):
		self.parent.Clear()
		self.parent.infoListView()
		self.parent.lst.SetFocus()

	def show_week_programlist(self, event):
		self.parent.lst.clear()
		selection = self.parent.cmb.GetSelection()
		if selection == None:
			return
		date = self.parent.clutl.dateToInteger(self.parent.result[selection])
		self.parent.progs.retrieveRadioListings(self.selected,date)
		title = self.parent.progs.gettitle() #番組のタイトル
		pfm = self.parent.progs.getpfm() #出演者の名前
		program_ftl = self.parent.progs.get_ftl()
		program_tol = self.parent.progs.get_tol()
		for t,p,ftl,tol in zip(title,pfm,program_ftl,program_tol):
			self.parent.lst.Append((t,p, ftl[:2]+":"+ftl[2:4],tol[:2]+":"+tol[2:4]), )

	def onbackbutton(self, event):
		self.parent.Clear()
		self.parent.area()
		self.parent.description()
		self.parent.volume, tmp = self.parent.creator.slider(_("音量(&V)"), event=self.onVolumeChanged, defaultValue=self.parent.app.config.getint("play", "volume", 100, 0, 100), textLayout=None)
		self.parent.volume.SetValue(self.parent.app.config.getint("play", "volume"))
		self.parent.menu.hRecordingFileTypeMenu.Check(self.parent.app.config.getint("recording", "menu_id"), self.parent.app.config.getboolean("recording", "check_menu"))
		self.parent.recorder.setFileType(self.parent.app.config.getint("recording", "menu_id")-10000)
		self.parent.exit_button()
		self.parent.SHOW_NOW_PROGRAMLIST()
		self.parent.AreaTreeCtrl()
		self.parent.getradio()
		#再生状態に応じて説明を表示
		if self.playing:
			self.show_description()
			self.parent.nplist.Enable()
			self.onReLoad(event)

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
		if self.id == None:
			return
		elif not self.recording:
			self.parent.menu.SetMenuLabel("RECORDING_IMMEDIATELY", _("録音を停止(&T)"))
			self.recording = True
			self.parent.get_streamUrl(self.id)
			replace = self.program_title.replace(" ","-")
			dirs = self.parent.recorder.create_recordingDir(self.parent.stid[self.id])
			self.parent.recorder.record(self.parent.m3u8, f"{dirs}\{str(datetime.date.today()) + replace}") #datetime+番組タイトルでファイル名を決定
		else:
			self.onRecordingStop()

	def onRecordingStop(self):
		self.parent.menu.SetMenuLabel("RECORDING_IMMEDIATELY", _("今すぐ録音(&R)"))
		self.parent.recorder.stop_record()
		self.recording = False
