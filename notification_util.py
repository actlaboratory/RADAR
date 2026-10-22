# -*- coding: utf-8 -*-
# 通知機能ユーティリティ
# wx.adv.NotificationMessageを使用したバルーン通知機能

import wx
import wx.adv
import constants
import globalVars

class NotificationUtil:
    """通知機能のユーティリティクラス"""

    def __init__(self):
        pass

    def notify(self, title, message, app_name='RADAR', timeout=10, speak=False):
        """通知を送信（wx のメインループ上で表示する）。

        speak=True のときはスクリーンリーダーでも読み上げる。通知がオフでも気づけるようにするため。
        """
        app_title = constants.APP_NAME if hasattr(constants, 'APP_NAME') else app_name
        # 何の通知か分かるよう、見出しを先頭に置く
        full_title = f"{title} - {app_title}" if title else app_title

        def do_show():
            notification = wx.adv.NotificationMessage(
                title=full_title,
                message=message
            )
            notification.Show(timeout)
            if speak:
                self._speak(title, message)

        app = wx.GetApp()
        if app is not None:
            wx.CallAfter(do_show)
        else:
            do_show()

    def _speak(self, title, message):
        app = getattr(globalVars, "app", None)
        if app is None or not hasattr(app, "say"):
            return
        text = message.replace("\n", " ")
        if title:
            text = f"{title}。{text}"
        try:
            # 操作中の読み上げを途中で切らないよう、割り込まずに続けて読む
            app.say(text, interrupt=False)
        except Exception:
            pass

# グローバルインスタンス
notification_util = NotificationUtil()

def notify(title, message, app_name='RADAR', timeout=10, speak=False):
    """通知を送信する便利関数"""
    notification_util.notify(title, message, app_name, timeout, speak)
