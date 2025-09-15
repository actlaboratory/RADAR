# Taskbar Icon

import wx
import wx.adv
import globalVars
import constants
from views.base import BaseMenu


class TaskbarIcon(wx.adv.TaskBarIcon):
	def __init__(self):
		super().__init__()
		self.icon = wx.Icon(constants.APP_ICON)
		self.SetIcon(self.icon, constants.APP_NAME)
		self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self.onDoubleClick)

	def CreatePopupMenu(self):
		menu = wx.Menu()
		
		# 表示メニュー
		show_item = menu.Append(wx.ID_ANY, _("表示(&S)"))
		menu.Bind(wx.EVT_MENU, self.onShow, show_item)
		
		# 終了メニュー
		exit_item = menu.Append(wx.ID_ANY, _("終了(&X)"))
		menu.Bind(wx.EVT_MENU, self.onExit, exit_item)
		
		return menu

	def onDoubleClick(self, event):
		globalVars.app.hMainView.events.show()

	def onShow(self, event):
		"""表示メニューが選択されたときの処理"""
		globalVars.app.hMainView.events.show()

	def onExit(self, event):
		"""終了メニューが選択されたときの処理"""
		globalVars.app.hMainView.events.exit(event)

	def setAlternateText(self, text=""):
		"""タスクバーアイコンに表示するテキストを変更する。「アプリ名 - 指定したテキスト」の形になる。

		:param text: アプリ名に続けて表示するテキスト
		:type text: str
		"""
		if text != "":
			text = " - " + text
		self.SetIcon(self.icon, constants.APP_NAME + text)
