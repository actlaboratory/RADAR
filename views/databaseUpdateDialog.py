# -*- coding: utf-8 -*-
# database update progress dialog

import wx

import constants
import views.ViewCreator

from views.baseDialog import BaseDialog


class databaseUpdateDialog(BaseDialog):
	def __init__(self):
		super().__init__("database_update_dialog")

	def Initialize(self, parent=None):
		super().Initialize(parent, _("番組データベース更新 - %s") % constants.APP_NAME)
		self.InstallControls()
		return True

	def InstallControls(self):
		self.creator = views.ViewCreator.ViewCreator(
			self.viewMode, self.panel, self.sizer, wx.VERTICAL, 5, style=wx.ALL, margin=20
		)
		self.statusStatic = self.creator.staticText(_("番組データベースを更新しています..."))
		self.gauge, self.gaugeStatic = self.creator.gauge(
			_("進行状況"), x=500, style=wx.TOP, margin=5, textLayout=None
		)
		self.gauge.SetRange(100)
		self.gauge.SetValue(0)

	def update_progress(self, current, total, status_text=None):
		if total > 0:
			self.gauge.SetRange(total)
			self.gauge.SetValue(min(current, total))
			percent = int(100 * current / total)
			self.gaugeStatic.SetLabel(_("進行状況 (%d/%d, %d%%)") % (current, total, percent))
		if status_text:
			self.statusStatic.SetLabel(status_text)
