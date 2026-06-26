# -*- coding: utf-8 -*-
# 通知機能ユーティリティ
# wx.adv.NotificationMessageを使用したバルーン通知機能

import wx
import wx.adv
import constants

class NotificationUtil:
    """通知機能のユーティリティクラス"""
    
    def __init__(self):
        pass
    
    def notify(self, title, message, app_name='RADAR', timeout=10):
        """通知を送信（wx のメインループ上で表示する）。"""
        def do_show():
            notification = wx.adv.NotificationMessage(
                title=constants.APP_NAME if hasattr(constants, 'APP_NAME') else app_name,
                message=message
            )
            notification.Show(timeout)

        app = wx.GetApp()
        if app is not None:
            wx.CallAfter(do_show)
        else:
            do_show()

# グローバルインスタンス
notification_util = NotificationUtil()

def notify(title, message, app_name='RADAR', timeout=10):
    """通知を送信する便利関数"""
    notification_util.notify(title, message, app_name, timeout)
