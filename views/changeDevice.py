# -*- coding: utf-8 -*-
# 再生デバイス変更ダイアログ

import wx
import globalVars
import views.ViewCreator
from views.baseDialog import *
from views.audio_output_devices import enumerate_playback_devices


class ChangeDeviceDialog(BaseDialog):
	def __init__(self):
		super().__init__("deviceDialog")

	def Initialize(self):
		self.log.debug("created")
		super().Initialize(self.app.hMainView.hFrame, _("再生デバイス変更"))
		self.InstallControls()
		return True

	def InstallControls(self):
		self.creator = views.ViewCreator.ViewCreator(
			self.viewMode, self.panel, self.sizer, wx.VERTICAL, 20,
			style=wx.EXPAND | wx.ALL, margin=20,
		)

		self.devices = enumerate_playback_devices()

		self.deviceList, self.static = self.creator.listCtrl(
			_("再生デバイス"),
			lambda e: None,
			wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_RAISED | wx.LC_NO_HEADER,
			size=(480, 260),
			sizerFlag=wx.EXPAND | wx.ALL,
			proportion=1,
		)
		self.deviceList.AppendColumn(_("再生デバイス"), width=450)
		self.deviceList.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.closeDialog)

		self.deviceList.InsertItem(0, _("規定のデバイス"))
		for i, d in enumerate(self.devices, start=1):
			self.deviceList.InsertItem(i, d["name"])

		current_id = ""
		if globalVars.app.config.has_section("livePlay"):
			current_id = globalVars.app.config.getstring("livePlay", "device_id", "")

		sel = 0
		if current_id:
			for i, d in enumerate(self.devices, start=1):
				if d["id"] == current_id:
					sel = i
					break
		self.deviceList.Focus(sel)
		self.deviceList.Select(sel)

		self.creator = views.ViewCreator.ViewCreator(
			self.viewMode, self.panel, self.sizer, wx.HORIZONTAL, 20, "",
			wx.ALIGN_RIGHT | wx.ALL, margin=20,
		)
		self.bOk = self.creator.okbutton(_("ＯＫ"), None)
		self.bCancel = self.creator.cancelbutton(_("キャンセル"), None)

	def GetData(self):
		idx = self.deviceList.GetFocusedItem()
		if idx in (wx.NOT_FOUND, -1):
			idx = 0
		if idx == 0:
			return None
		return self.devices[idx - 1]

	def closeDialog(self, event):
		self.wnd.EndModal(wx.ID_OK)
