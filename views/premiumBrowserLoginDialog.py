# -*- coding: utf-8 -*-
# radiko プレミアム ブラウザでのログインダイアログ
#
# radiko のログインページを埋め込みブラウザ (Edge WebView2) で開き、利用者に直接ログインしてもらう。
# Cloudflare のチャレンジ (Turnstile) はブラウザ上で処理されるため、アプリからのログイン API 呼び出しが
# 拒否されるようになってもログインできる。ログイン後の radiko_session を Cookie から取り出して使う。

import json

import wx
import wx.html2

import views.ViewCreator
from views.baseDialog import *
from views import premium
import simpleDialog

# ログイン状態を確かめる間隔（ミリ秒）。ログイン後の画面遷移はページ読み込みを伴わない場合があるため定期的に確かめる
CHECK_INTERVAL_MS = 2000
MESSAGE_HANDLER = "radar"

# ページ内で実行し、ログイン済みなら radiko_session をアプリへ送る
CHECK_SCRIPT = """
(function () {
	if (location.hostname !== "radiko.jp" || !window.%(handler)s) {
		return;
	}
	fetch("/ap/member/webapi/v2/member/login/check", {credentials: "same-origin", cache: "no-store"})
		.then(function (r) { return r.ok ? r.json() : null; })
		.then(function (data) {
			if (!data || String(data.status) !== "200") {
				return;
			}
			var m = document.cookie.match(/(?:^|;\\s*)radiko_session=([^;]+)/);
			window.%(handler)s.postMessage(JSON.stringify({
				session: m ? decodeURIComponent(m[1]) : "",
				areafree: data.areafree
			}));
		})
		.catch(function () {});
})();
""" % {"handler": MESSAGE_HANDLER}


def is_available():
	"""埋め込みブラウザ (Edge WebView2) が使えるか"""
	try:
		return wx.html2.WebView.IsBackendAvailable(wx.html2.WebViewBackendEdge)
	except Exception:
		return False


class Dialog(BaseDialog):
	def __init__(self):
		super().__init__("premiumBrowserLoginDialog")
		self.radiko_session = None
		self.areafree = None
		self.timer = None
		# 最初のページが読み込まれるまではスクリプトを実行しない（WebView2 の初期化前に実行すると失敗する）
		self.loaded = False
		# 結果が確定したら以降のメッセージを無視する（タイマーと読み込み完了の両方から確認が走るため、複数届くことがある）
		self.finished = False

	def Initialize(self):
		self.log.debug("created")
		super().Initialize(self.app.hMainView.hFrame, _("ブラウザでradikoにログイン"), style=wx.CAPTION | wx.SYSTEM_MENU | wx.BORDER_DEFAULT | wx.RESIZE_BORDER)
		self.InstallControls()
		return True

	def InstallControls(self):
		"""いろんなwidgetを設置する。"""
		self.creator = views.ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.VERTICAL, 20, style=wx.ALL | wx.EXPAND, margin=20)
		self.creator.staticText(_("表示されたradikoのページで、エリアフリープランを契約しているアカウントにログインしてください。ログインが確認できると、このウィンドウは自動で閉じます。"), x=760)

		self.web = wx.html2.WebView.New(self.panel, size=(800, 600), backend=wx.html2.WebViewBackendEdge)
		self.sizer.Add(self.web, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 20)
		self.web.Bind(wx.html2.EVT_WEBVIEW_LOADED, self.onLoaded)
		self.web.Bind(wx.html2.EVT_WEBVIEW_NEWWINDOW, self.onNewWindow)
		self.web.Bind(wx.html2.EVT_WEBVIEW_SCRIPT_MESSAGE_RECEIVED, self.onScriptMessage)
		if not self.web.AddScriptMessageHandler(MESSAGE_HANDLER):
			self.log.error("failed to add script message handler")

		self.creator = views.ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.HORIZONTAL, 20, style=wx.ALIGN_RIGHT)
		self.bReload = self.creator.button(_("再読み込み(&R)"), self.reload)
		self.bCancel = self.creator.cancelbutton(_("キャンセル"), None)

		self.timer = wx.Timer(self.wnd)
		self.wnd.Bind(wx.EVT_TIMER, self.onTimer, self.timer)
		self.timer.Start(CHECK_INTERVAL_MS)

		self.web.LoadURL(premium.WEB_LOGIN_URL)
		self.web.SetFocus()

	def reload(self, event):
		self.web.LoadURL(premium.WEB_LOGIN_URL)
		self.web.SetFocus()

	def onLoaded(self, event):
		self.log.debug("page loaded")
		self.loaded = True
		self.checkLogin()
		event.Skip()

	def onNewWindow(self, event):
		# 新しいウィンドウを開くリンクは同じ画面で開く
		self.web.LoadURL(event.GetURL())

	def onTimer(self, event):
		self.checkLogin()

	def checkLogin(self):
		if self.finished or not self.loaded:
			return
		try:
			self.web.RunScriptAsync(CHECK_SCRIPT)
		except Exception as e:
			self.log.warning("failed to run login check script: %s", e)

	def onScriptMessage(self, event):
		if self.finished or event.GetMessageHandler() != MESSAGE_HANDLER:
			return
		try:
			data = json.loads(event.GetString())
		except ValueError:
			self.log.warning("invalid message from login page")
			return
		self.finished = True
		self.timer.Stop()
		session = str(data.get("session") or "").strip()
		if not session:
			self.log.error("logged in on browser but radiko_session cookie is not readable")
			simpleDialog.errorDialog(_("ログインは確認できましたが、ログイン情報を取得できませんでした。\nradiko側の仕様変更により、ブラウザでのログインが使えなくなった可能性があります。"), self.wnd)
			self.wnd.EndModal(wx.ID_CANCEL)
			return
		self.log.info("browser login detected")
		self.radiko_session = session
		self.areafree = premium.is_areafree_flag(data.get("areafree"))
		self.wnd.EndModal(wx.ID_OK)

	def Destroy(self):
		if self.timer:
			self.timer.Stop()
		super().Destroy()

	def GetData(self):
		return self.radiko_session, self.areafree

	def GetValue(self):
		# 基底クラスは値をログに出力するため、セッションを含む値はログに残さずに返す
		return self.value
