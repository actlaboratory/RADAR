# -*- coding: utf-8 -*-
# radiko プレミアム ログインダイアログ

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
		# 入力し直せるよう、ログインに失敗してもダイアログは閉じない
		try:
			with wx.BusyCursor():
				premium.session.login(self.mail.GetValue().strip(), self.password.GetValue())
		except premium.PremiumAuthError as e:
			if e.kind != premium.PremiumAuthError.BLOCKED or not premiumBrowserLoginDialog.is_available():
				simpleDialog.errorDialog(premium.describe_auth_error(e), self.wnd)
				if e.kind == premium.PremiumAuthError.INVALID_CREDENTIALS:
					self.password.SetFocus()
					self.password.SelectAll()
				return
			# アプリからのログインがボット対策で拒否された。ブラウザでのログインに切り替えてもらう
			result = simpleDialog.yesNoDialog(
				_("ログイン"),
				premium.describe_auth_error(e) + "\n\n" + _("ブラウザでログインしますか？"),
				self.wnd,
			)
			if result == wx.ID_YES:
				self.browser(event)
			return
		self.method = premium.METHOD_PASSWORD
		event.Skip()

	def browser(self, event):
		self.method = premium.METHOD_BROWSER
		self.wnd.EndModal(wx.ID_OK)

	def GetData(self):
		return self.method, self.mail.GetValue().strip(), self.password.GetValue(), self.save.GetValue()

	def GetValue(self):
		# 基底クラスは値をログに出力するため、パスワードを含む値はログに残さずに返す
		return self.value
