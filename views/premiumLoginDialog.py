# -*- coding: utf-8 -*-
# radiko プレミアム ログインダイアログ

import wx

import views.ViewCreator
from views.baseDialog import *
import simpleDialog


class Dialog(BaseDialog):
	def __init__(self, defaultMail=""):
		super().__init__("premiumLoginDialog")
		self.defaultMail = defaultMail

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
		self.save = self.creator.checkbox(_("認証情報を保存し、起動時に自動でログインする(&S)"), state=True)

		self.creator = views.ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.HORIZONTAL, 20, style=wx.ALIGN_RIGHT)
		self.bOk = self.creator.okbutton(_("ログイン"), self.ok)
		self.bCancel = self.creator.cancelbutton(_("キャンセル"), None)

	def ok(self, event):
		if not self.mail.GetValue().strip() or not self.password.GetValue():
			simpleDialog.errorDialog(_("メールアドレスとパスワードを入力してください。"), self.wnd)
			return
		event.Skip()

	def GetData(self):
		return self.mail.GetValue().strip(), self.password.GetValue(), self.save.GetValue()
