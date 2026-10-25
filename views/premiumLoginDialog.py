# -*- coding: utf-8 -*-
# radiko プレミアム ログインダイアログ

import threading

import wx

import views.ViewCreator
from views.baseDialog import *
from views import premium
from views import premiumBrowserLoginDialog
import simpleDialog


class Dialog(BaseDialog):
	def __init__(self, defaultMail="", defaultSave=True):
		super().__init__("premiumLoginDialog")
		self.defaultMail = defaultMail
		self.defaultSave = defaultSave
		self.method = premium.METHOD_PASSWORD
		# ログインの通信を別スレッドで行っている間は、続けて押されても始めない
		self._logging_in = False

	def Initialize(self):
		self.log.debug("created")
		super().Initialize(self.app.hMainView.hFrame, _("radikoプレミアムにログイン"))
		self.InstallControls()
		return True

	def InstallControls(self):
		"""いろんなwidgetを設置する。"""
		self.creator = views.ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.VERTICAL, 20, style=wx.ALL | wx.EXPAND, margin=20)
		self.creator.staticText(_("エリアフリープランを契約しているradikoプレミアム会員のアカウントでログインしてください。"))
		self.mail, self.mailStatic = self.creator.inputbox(_("メールアドレス(&M)"), defaultValue=self.defaultMail, x=400)
		self.password, self.passwordStatic = self.creator.inputbox(_("パスワード(&P)"), x=400, style=wx.TE_PASSWORD)
		self.save = self.creator.checkbox(_("ログイン情報を保存し、起動時に自動でログインする(&S)"), state=self.defaultSave)
		self.creator.staticText(_("ログインできない場合は、「ブラウザでログイン」からradikoのページで直接ログインしてください。\nブラウザでログインした場合は、セッションが切れたときに再度ログインが必要です。"),proportion=1)

		self.creator = views.ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.HORIZONTAL, 20, style=wx.ALIGN_RIGHT)
		self.bOk = self.creator.okbutton(_("ログイン"), self.ok)
		self.bBrowser = self.creator.button(_("ブラウザでログイン(&B)"), self.browser)
		self.bCancel = self.creator.cancelbutton(_("キャンセル"), None)
		if not premiumBrowserLoginDialog.is_available():
			self.bBrowser.Disable()

	def ok(self, event):
		if not self.mail.GetValue().strip() or not self.password.GetValue():
			simpleDialog.errorDialog(_("メールアドレスとパスワードを入力してください。"), self.wnd)
			return
		if self._logging_in:
			self.app.say(_("ログインしています"), interrupt=True)
			return
		# ログインの通信には最大で数十秒かかるため、別スレッドで行い画面を止めない。
		# 入力し直せるよう、ログインに失敗してもダイアログは閉じない
		self._logging_in = True
		self.app.say(_("ログインしています"), interrupt=True)
		threading.Thread(
			target=self._login_worker,
			args=(self.mail.GetValue().strip(), self.password.GetValue()),
			name="PremiumLogin",
			daemon=True,
		).start()

	def _login_worker(self, mail, password):
		"""（別スレッド）ログインし、結果を UI スレッドへ渡す"""
		try:
			premium.session.login(mail, password)
			error = None
		except Exception as e:
			error = e
		wx.CallAfter(self._finish_login, error)

	def _is_alive(self):
		try:
			return bool(self.wnd) and self.wnd.IsModal()
		except RuntimeError:
			return False

	def _finish_login(self, error):
		"""（UI スレッド）ログインの結果を反映する"""
		self._logging_in = False
		if not self._is_alive():
			# ログイン中にキャンセルされた。ログインできていたら取り消す
			if error is None:
				self.log.info("premium login finished after the dialog was cancelled; logging out")
				threading.Thread(target=premium.session.logout, name="PremiumLogout", daemon=True).start()
			return
		if error is None:
			self.method = premium.METHOD_PASSWORD
			self.wnd.EndModal(wx.ID_OK)
			return
		if not isinstance(error, premium.PremiumAuthError):
			self.log.error(f"Unexpected error during premium login: {error!r}")
			simpleDialog.errorDialog(_("ログインに失敗しました。") + f"\n{error}", self.wnd)
			return
		if error.kind != premium.PremiumAuthError.BLOCKED or not premiumBrowserLoginDialog.is_available():
			simpleDialog.errorDialog(premium.describe_auth_error(error), self.wnd)
			if error.kind == premium.PremiumAuthError.INVALID_CREDENTIALS:
				self.password.SetFocus()
				self.password.SelectAll()
			return
		# アプリからのログインがボット対策で拒否された。ブラウザでのログインに切り替えてもらう
		result = simpleDialog.yesNoDialog(
			_("ログイン"),
			premium.describe_auth_error(error) + "\n\n" + _("ブラウザでログインしますか？"),
			self.wnd,
		)
		if result == wx.ID_YES:
			self.browser(None)

	def browser(self, event):
		if self._logging_in:
			# パスワードでのログインの結果を待っている。先にダイアログを閉じると、その結果を取り消せない
			self.app.say(_("ログインしています"), interrupt=True)
			return
		self.method = premium.METHOD_BROWSER
		self.wnd.EndModal(wx.ID_OK)

	def GetData(self):
		return self.method, self.mail.GetValue().strip(), self.password.GetValue(), self.save.GetValue()

	def GetValue(self):
		# 基底クラスは値をログに出力するため、パスワードを含む値はログに残さずに返す
		return self.value
