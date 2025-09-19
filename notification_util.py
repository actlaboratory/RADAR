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
        """通知を送信"""
        notification = wx.adv.NotificationMessage(
            title=constants.APP_NAME if hasattr(constants, 'APP_NAME') else app_name,
            message=message
        )
        notification.Show(timeout)
        notification.Close()

# グローバルインスタンス
notification_util = NotificationUtil()

def notify(title, message, app_name='RADAR', timeout=10):
    """通知を送信する便利関数"""
    notification_util.notify(title, message, app_name, timeout)
