# -*- coding: utf-8 -*-
# main view
# Copyright (C) 2019 Yukio Nozawa <personal@nyanchangames.com>
# Copyright (C) 2019-2021 yamahubuki <itiro.ishino@gmail.com>

import wx
from views import play
import xml.etree.ElementTree as ET
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
		self.area()
		self.exit_button()
		self.AreaTreeCtrl()
		self.getradio()
		self.player()


	def AreaTreeCtrl(self):
		self.tree,broadcaster = self.creator.treeCtrl(_("放送エリア"))

	def getradio(self):
		"""ステーションidを取得後、ツリービューに描画"""
		self.log.info("currentAreaId:"+self.result[:4])
		#broadcast_dic = {}
		#ツリーのルート項目の作成
		root = self.tree.AddRoot(_("放送局一覧"))

		url = "https://radiko.jp/v3/station/region/full.xml" #放送局リストurl
		#xmlから情報取得
		req = request.Request(url) 
		with request.urlopen(req) as response:
			xml_data = response.read().decode() #デフォルトではbytesオブジェクトなので文字列へのデコードが必要
			#print(xml_data)
			parsed = ET.fromstring(xml_data)
			for child in parsed:
				for i in child:
					#エリアidをキー、ステーションidを値に設定
					broadcast_dic = {i[16].text:i[1].text}
					#ユーザーが実行しているエリアで放送されている番組を辞書から探してツリーに描画
					if self.result[:4] in broadcast_dic:
						self.tree.AppendItem(root, broadcast_dic[self.result[:4]])

		self.tree.SetFocus()
		self.tree.Expand(root)
		self.tree.SelectItem(root, select=True)

	def area(self):
		self.play = play.Play()
		res = self.play.auth1()
		ret = self.play.get_partial_key(res)
		self.token = ret[1]
		self.partialkey = ret[0]
		self.play.auth2(self.partialkey, self.token )
		self.result = self.play.area
		self.log.info(self.result)

	def player(self):
		"""再生用関数"""
		if len(sys.argv) > 1:
			url = f'http://f-radiko.smartstream.ne.jp/{sys.argv[1]}/_definst_/simul-stream.stream/playlist.m3u8'
			m3u8 = self.play.gen_temp_chunk_m3u8_url( url ,self.token)
			subprocess.run(["ffplay", "-nodisp", "-loglevel", "quiet", "-headers", f"X-Radiko-Authtoken:{self.token}", "-i", m3u8], shell=True)


	def exit_button(self):
		self.exitbtn = self.creator.button(_("終了"), self.events.exit)


class Menu(BaseMenu):
	def Apply(self, target):
		"""指定されたウィンドウに、メニューを適用する。"""

		# メニュー内容をいったんクリア
		self.hMenuBar = wx.MenuBar()

		# メニューの大項目を作る
		self.hFileMenu = wx.Menu()
		self.hOptionMenu = wx.Menu()
		self.hHelpMenu = wx.Menu()

		# ファイルメニュー
		self.RegisterMenuCommand(self.hFileMenu, {
			"FILE_EXAMPLE": self.parent.events.example,
			"FILE_EXIT": self.parent.events.exit,
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
		self.hMenuBar.Append(self.hOptionMenu, _("オプション(&O)"))
		self.hMenuBar.Append(self.hHelpMenu, _("ヘルプ(&H)"))
		target.SetMenuBar(self.hMenuBar)


class Events(BaseEvents):
	def example(self, event):
		d = sample.Dialog()
		d.Initialize()
		r = d.Show()

	def exit(self, event):
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
