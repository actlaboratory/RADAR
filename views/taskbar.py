# Taskbar Icon

import wx
import wx.adv
import globalVars
import constants
from views.base import BaseMenu
import menuItemsStore
from logging import getLogger


class TaskbarIcon(wx.adv.TaskBarIcon):
	def __init__(self):
		super().__init__()
		self.log = getLogger("%s.%s" % (constants.LOG_PREFIX, "taskbar"))
		self.icon = wx.Icon(constants.APP_ICON)
		self.SetIcon(self.icon, constants.APP_NAME)
		self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self.onDoubleClick)

	def CreatePopupMenu(self):
		bm = BaseMenu("mainView")
		menu = wx.Menu()
		menu.Bind(wx.EVT_MENU, self.TbMenuSelect)
		bm.RegisterMenuCommand(menu, [
			"SHOW", "EXIT",
		])
		return menu

	def onDoubleClick(self, event):
		globalVars.app.hMainView.events.show()

	def setAlternateText(self, text=""):
		"""タスクバーアイコンに表示するテキストを変更する。「アプリ名 - 指定したテキスト」の形になる。

		:param text: アプリ名に続けて表示するテキスト
		:type text: str
		"""
		if text != "":
			text = " - " + text
		self.SetIcon(self.icon, constants.APP_NAME + text)

	def TbMenuSelect(self, event):
		event_id = event.GetId()
		if event_id == menuItemsStore.getRef("SHOW"):
			self.log.info("selected:SHOW")
			self.onDoubleClick(event)
			return
		if event_id == menuItemsStore.getRef("EXIT"):
			self.log.info("selected:EXIT")
			globalVars.app.hMainView.events.exit()
			return