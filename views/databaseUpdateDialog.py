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
		self.statusStatic = self.creator.staticText(_("番組データベースを更新しています...\n"))
		gauge_style = wx.GA_HORIZONTAL | wx.GA_SMOOTH
		self.gauge, _gauge_label = self.creator.gauge(
			_("進行状況"),
			x=500,
			style=gauge_style,
			margin=5,
			textLayout=None,
		)
		self.gauge.SetRange(100)
		self.gauge.SetValue(0)

	def set_status_text(self, text):
		self.statusStatic.SetLabel(text)

	def update_progress(self, current, total, status_text=None):
		if total > 0:
			self.gauge.SetRange(total)
			self.gauge.SetValue(min(current, total))
			percent = min(100, int(100 * current / total))
			if status_text:
				self.statusStatic.SetLabel(
					_("%(status)s\n(%(current)d/%(total)d, %(percent)d%%)") % {
						"status": status_text,
						"current": current,
						"total": total,
						"percent": percent,
					}
				)
			else:
				self.statusStatic.SetLabel(
					_("進捗\n%(current)d/%(total)d (%(percent)d%%)") % {
						"current": current,
						"total": total,
						"percent": percent,
					}
				)
		elif status_text:
			self.statusStatic.SetLabel(status_text)
