# -*- coding: utf-8 -*-
# database update progress UI

import threading

import wx

from views.databaseUpdateDialog import databaseUpdateDialog


class DatabaseUpdateProgress:
	"""番組DB更新の進捗表示を管理する。"""

	def __init__(self):
		self.dialog = None
		self._active = False

	def start(self, parent=None):
		if self._active:
			return
		if threading.current_thread() is threading.main_thread():
			self._start_ui(parent)
		else:
			wx.CallAfter(self._start_ui, parent)

	def _start_ui(self, parent):
		if self._active:
			return
		self._active = True
		self.dialog = databaseUpdateDialog()
		self.dialog.Initialize(parent)
		self.dialog.Show(modal=False)
		self.dialog.set_status_text(_("番組データベースを更新しています"))

	def report(self, current, total, status_text=None):
		if not self._active:
			return
		if threading.current_thread() is threading.main_thread():
			self._report_ui(current, total, status_text)
		else:
			wx.CallAfter(self._report_ui, current, total, status_text)

	def _report_ui(self, current, total, status_text=None):
		if not self._active or not self.dialog:
			return
		self.dialog.update_progress(current, total, status_text)
		wx.YieldIfNeeded()

	def finish(self, success=True):
		if threading.current_thread() is threading.main_thread():
			self._finish_ui(success)
		else:
			wx.CallAfter(self._finish_ui, success)

	def _finish_ui(self, success):
		if self.dialog:
			if success:
				self.dialog.set_status_text(_("番組データベースの更新が完了しました"))
			else:
				self.dialog.set_status_text(_("番組データベースの更新に失敗しました"))
			wx.YieldIfNeeded()
			try:
				self.dialog.Destroy()
			except Exception:
				pass
			self.dialog = None
		self._active = False
