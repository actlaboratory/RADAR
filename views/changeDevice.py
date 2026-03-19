# -*- coding: utf-8 -*-
# 再生デバイス変更ダイアログ

import wx
import globalVars
import views.ViewCreator
from logging import getLogger
from views.baseDialog import *
from views.mpvPlayer import getDeviceList

class ChangeDeviceDialog(BaseDialog):
	def __init__(self):
		super().__init__("deviceDialog")

	def Initialize(self):
		self.log.debug("created")
		super().Initialize(self.app.hMainView.hFrame,_("再生デバイス変更"))
		self.InstallControls()
		return True

	def InstallControls(self):
		"""いろんなwidgetを設置する。"""
		self.creator=views.ViewCreator.ViewCreator(self.viewMode,self.panel,self.sizer,wx.VERTICAL,20,style=wx.EXPAND|wx.ALL,margin=20)
		self.deviceList, self.static = self.creator.listCtrl(_("再生デバイス"), None, wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_RAISED | wx.LC_NO_HEADER,sizerFlag=wx.EXPAND)
		self.deviceList.AppendColumn(_("再生デバイス"),width=450)
		self.deviceList.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.closeDialog)
		self.devices = getDeviceList()
		self.deviceList.InsertItem(0, _("規定のデバイス"))
		for d in self.devices:
			self.deviceList.Append([d["name"]])

		try:
			if globalVars.app.config.has_section("livePlay"):
				current_id = globalVars.app.config.getstring("livePlay", "device_id", "")
			else:
				current_id = ""
		except Exception:
			current_id = ""

		idx = 0
		if current_id:
			for i, d in enumerate(self.devices, start=1):
				if d["id"] == current_id:
					idx = i
					break
		self.deviceList.Focus(idx)
		self.deviceList.Select(idx)

		self.creator=views.ViewCreator.ViewCreator(self.viewMode,self.panel,self.sizer,wx.HORIZONTAL,20,"",wx.ALIGN_RIGHT|wx.ALL,margin=20)
		self.bOk=self.creator.okbutton(_("ＯＫ"),None)
		self.bCancel=self.creator.cancelbutton(_("キャンセル"),None)

	def GetData(self):
		selected = self.deviceList.GetFocusedItem()
		if selected == 0:
			return None
		else:
			return self.devices[selected - 1]

	def closeDialog(self, event):
		self.wnd.EndModal(wx.ID_OK)
