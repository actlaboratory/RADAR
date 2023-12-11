# -*- coding: utf-8 -*-
# main view
# Copyright (C) 2019 Yukio Nozawa <personal@nyanchangames.com>
# Copyright (C) 2019-2021 yamahubuki <itiro.ishino@gmail.com>

import wx
from lxml import html
import re
from views import token
from views import programmanager
import xml.etree.ElementTree as ET
from itertools import islice
import subprocess

import constants
import globalVars
import update
import menuItemsStore

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
		self.progs = programmanager.ProgramManager()
		self.area()
		self.volume, tmp = self.creator.slider(_("音量(&V)"), event=self.events.onVolumeChanged, defaultValue=self.app.config.getint("play", "volume", 100, 0, 100), textLayout=None)
		self.playbutton()
		self.stopbutton()

		self.exit_button()
		self.AreaTreeCtrl()
		self.getradio()

	def AreaTreeCtrl(self):
		self.tree,broadcaster = self.creator.treeCtrl(_("放送エリア"))

	def infoListView(self):
		self.lst,programinfo = self.creator.virtualListCtrl(_("番組表一覧"))
		self.lst.AppendColumn(_("タイトル"))
		self.lst.AppendColumn(_("出演者"))
		self.backbtn()

	def backbtn(self):
		self.bkbtn = self.creator.button(_("前の画面に戻る"), self.events.onbackbutton)

	def playbutton(self):
		self.playButton = self.creator.button(_("再生"), self.events.onRadioActivated)

	def stopbutton(self):
		self.stopButton = self.creator.button(_("停止"), self.events.onStopButton)

		return

	def getradio(self):
		"""ステーションidを取得後、ツリービューに描画"""
		#都道府県をキー、地方を値とする辞書を作成
		region = {
			"hokkaido":"HOKKAIDO TOHOKU",
			"aomori":"HOKKAIDO TOHOKU",
			"iwate":"HOKKAIDO TOHOKU",
			"akita":"HOKKAIDO TOHOKU",
			"miyagi":"HOKKAIDO TOHOKU",
			"fukusima":"HOKKAIDO TOHOKU",
			"yamagata":"HOKKAIDO TOHOKU",
			"ibaraki":"KANTO",
			"gunma":"KANTO",
			"totigi":"KANTO",
			"saitama":"KANTO",
			"chiba":"KANTO",
			"toukyou":"KANTO",
			"kanagawa":"KANTO",
			"shizuoka":"chubu",
			"aichi":"chubu",
			"gifu":"chubu",
			"nagano":"chubu",
			"yamanashi":"chubu",
			"ishikawa":"HOKURIKU KOUSHINETSU",
			"niigata":"HOKURIKU KOUSHINETSU",
			"toyama":"HOKURIKU KOUSHINETSU",
			"fukui":"HOKURIKU KOUSHINETSU",
			"mie":"KINKI",
			"shiga":"KINKI",
			"kyoto":"KINKI,",
			"osaka":"KINKI",
			"hyogo":"KINKI",
			"wakayama":"KINKI",
			"nara":"KINKI",
			"tottori":"CHUGOKU SHIKOKU",
			"shimane":"CHUGOKU SHIKOKU",
			"hiroshima":"CHUGOKU SHIKOKU",
			"okayama":"CHUGOKU SHIKOKU",
			"yamaguchi":"CHUGOKU SHIKOKU",
			"kagawa":"CHUGOKU SHIKOKU",
			"kochi":"CHUGOKU SHIKOKU",
			"ehime":"CHUGOKU SHIKOKU",
			"tokushima":"CHUGOKU SHIKOKU",
			"fukuoka":"KYUSHU",
			"oita":"KYUSHU",
			"saga":"KYUSHU",
			"nagasaki":"KYUSHU",
			"miyazaki":"KYUSHU",
			"kagoshima":"KYUSHU",
			"okinawa":"KYUSHU",
			"zenkoku":"ZENKOKU"
		}
		if self.result in region:
			self.log.info("region:"+region[self.result])
		#ツリーのルート項目の作成
		root = self.tree.AddRoot(_("放送局一覧"))
		if not self.result:
			errorDialog(_("エリア情報の取得に失敗しました。\nインターネットの接続状況をご確認ください"))
			self.tree.SetFocus()
			self.tree.Expand(root)
			self.tree.SelectItem(root, select=True)
			return

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
					if region[self.result] in stream:
						self.tree.AppendItem(root, stream[region[self.result]]["radioname"], data=stream[region[self.result]]["radioid"])
		self.tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self.events.onRadioActivated)
		self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.events.onRadioSelected)
		self.tree.SetFocus()
		self.tree.Expand(root)
		self.tree.SelectItem(root, select=True)

	def area(self):
		self.gettoken = token.Token()
		res = self.gettoken.auth1()
		ret = self.gettoken.get_partial_key(res)
		self.token = ret[1]
		self.partialkey = ret[0]
		self.gettoken.auth2(self.partialkey, self.token )
		area = self.gettoken.area #エイラを取得
		before = re.findall("\s", area)
		replace = area.replace(before[0], ",") #スペースを文字列置換で,に置き換える
		values = replace.split(",") #戻り地をリストにする
		self.result = values[2]

	def player(self, stationid):
		"""再生用関数"""
		url = f'http://f-radiko.smartstream.ne.jp/{stationid}/_definst_/simul-stream.stream/playlist.m3u8'
		m3u8 = self.gettoken.gen_temp_chunk_m3u8_url( url ,self.token)
		self._player.setSource(m3u8)
		self._player.play()


	def exit_button(self):
		self.exitbtn = self.creator.button(_("終了"), self.events.exit)


class Menu(BaseMenu):
	def Apply(self, target):
		"""指定されたウィンドウに、メニューを適用する。"""

		# メニュー内容をいったんクリア
		self.hMenuBar = wx.MenuBar()

		# メニューの大項目を作る
		self.hFileMenu = wx.Menu()
		self.hFunctionMenu = wx.Menu()
		self.hProgramListMenu = wx.Menu()
		self.hOptionMenu = wx.Menu()
		self.hHelpMenu = wx.Menu()

		# ファイルメニュー
		self.RegisterMenuCommand(self.hFileMenu, {
			"FILE_EXAMPLE": self.parent.events.example,
			"FILE_EXIT": self.parent.events.exit,
		})

		#機能メニュー
		self.RegisterMenuCommand(self.hFunctionMenu, {
			"FUNCTION_PLAY_PLAY":self.parent.events.onRadioActivated,
			"FUNCTION_PLAY_POSE":self.parent.events.onStopButton,
			"FUNCTION_VOLUME_UP":self.parent.events.volume_up,
			"FUNCTION_VOLUME_DOWN":self.parent.events.volume_down,
		})

		#番組メニュー
		self.RegisterMenuCommand(self.hProgramListMenu, {
			"SHOW_NOW_PROGRAMLIST":self.parent.events.nowProgramInfo,
			"SHOW_TOMORROW_PROGRAMLIST":self.parent.events.tomorrowProgramInfo,
		})

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
		self.hMenuBar.Append(self.hOptionMenu, _("オプション(&O)"))
		self.hMenuBar.Append(self.hHelpMenu, _("ヘルプ(&H)"))
		target.SetMenuBar(self.hMenuBar)


class Events(BaseEvents):
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
		id = self.parent.tree.GetItemData(self.parent.tree.GetFocusedItem()) #stationIDが出る
		if id == None:
			return
		self.parent.player(id)
		self.log.info("now playing:"+id)
	def onRadioSelected(self, event):
		selected = self.parent.tree.GetItemData(self.parent.tree.GetFocusedItem())
		if selected == None:
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_NOW_PROGRAMLIST"),False)
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_TOMORROW_PROGRAMLIST"),False)
			return
		self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_NOW_PROGRAMLIST"), True)
		self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_TOMORROW_PROGRAMLIST"),True)
		self.parent.progs.getTodayProgramList(selected)

	def onStopButton(self, event):
		self.parent._player.stop()

	def onVolumeChanged(self, event):
		self.value = self.parent.volume.GetValue()
		self.parent._player.setVolume(self.value)


	def volume_up(self, event):
		self.onVolumeChanged(event)
		if self.value == self.parent.volume.GetMax():
			return
		self.parent.volume.SetValue(self.value+10)

	def volume_down(self, event):
		self.onVolumeChanged(event)
		if self.value == self.parent.volume.GetMin():
			return
		self.parent.volume.SetValue(self.value-10)

	def nowProgramInfo(self, event):
		title = self.parent.progs.gettitle() #番組のタイトル
		pfm = self.parent.progs.getpfm() #出演者の名前
		self.parent.Clear()
		self.parent.infoListView()

		for t,p in zip(title,pfm):
			self.parent.lst.Append((t,p), )
		self.parent.lst.SetFocus()

	def tomorrowProgramInfo(self, event):
		print("tomorrow")

	def onbackbutton(self, event):
		self.parent.Clear()
		self.parent.area()
		self.parent.volume, tmp = self.parent.creator.slider(_("音量(&V)"), event=self.onVolumeChanged, defaultValue=self.parent.app.config.getint("play", "volume", 100, 0, 100), textLayout=None)
		self.parent.playbutton()
		self.parent.stopbutton()
		self.parent.exit_button()
		self.parent.AreaTreeCtrl()
		self.parent.getradio()
