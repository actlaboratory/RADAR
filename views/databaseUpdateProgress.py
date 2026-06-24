# -*- coding: utf-8 -*-
# database update progress and accessible output

import threading

import wx

import globalVars
from views.databaseUpdateDialog import databaseUpdateDialog


class DatabaseUpdateProgress:
	"""番組DB更新の進捗表示と accessible_output2 による読み上げを管理する。"""

	def __init__(self):
		self.dialog = None
		self._active = False
		self._last_spoken_percent = -1

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
		self._last_spoken_percent = -1
		self.dialog = databaseUpdateDialog()
		self.dialog.Initialize(parent)
		self.dialog.Show(modal=False)
		self._say(_("番組データベースを更新しています"), interrupt=True)

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
		if total > 0:
			percent = min(100, int(100 * current / total))
			spoken_bucket = percent // 10
			if spoken_bucket > self._last_spoken_percent // 10 or current >= total:
				self._last_spoken_percent = percent
				if status_text:
					self._say(status_text, interrupt=True)
				else:
					self._say(_("進捗 %(percent)dパーセント") % {"percent": percent}, interrupt=True)

	def finish(self, success=True):
		if threading.current_thread() is threading.main_thread():
			self._finish_ui(success)
		else:
			wx.CallAfter(self._finish_ui, success)

	def _finish_ui(self, success):
		if success:
			self._say(_("番組データベースの更新が完了しました"), interrupt=False)
		else:
			self._say(_("番組データベースの更新に失敗しました"), interrupt=True)
		if self.dialog:
			try:
				self.dialog.Destroy()
			except Exception:
				pass
			self.dialog = None
		self._active = False
		self._last_spoken_percent = -1

	def _say(self, text, interrupt=False):
		try:
			if hasattr(globalVars, "app") and globalVars.app:
				globalVars.app.say(text, interrupt=interrupt)
		except Exception:
			pass
