# -*- coding: utf-8 -*-
# 音量制御ハンドラーモジュール
# Copyright (C) 2019 Yukio Nozawa <personal@nyanchangames.com>
# Copyright (C) 2019-2021 yamahubuki <itiro.ishino@gmail.com>

import wx
from views import changeDevice


class VolumeHandler:
    def __init__(self, parent_view):
        self.parent = parent_view
        self.log = parent_view.log
        self.app = parent_view.app
        self.events = parent_view.events

    def onVolumeChanged(self, event):
        """音量変更時の処理"""
        value = self.parent.radio_manager.volume.GetValue()
        self.parent.radio_manager._player.setVolume(value)
        self.parent.app.config["play"]["volume"] = value

    def volume_up(self, event):
        """音量を上げる"""
        value = self.parent.radio_manager.volume.GetValue()
        if value == self.parent.radio_manager.volume.GetMax():
            return
        self.parent.radio_manager.volume.SetValue(value + 10)  # ボリュームを10％上げる
        self.onVolumeChanged(event)
        self.log.debug("volume increased")

    def volume_down(self, event):
        """音量を下げる"""
        value = self.parent.radio_manager.volume.GetValue()
        if value == self.parent.radio_manager.volume.GetMin():
            return
        self.parent.radio_manager.volume.SetValue(value - 10)  # ボリュームを10％下げる
        self.onVolumeChanged(event)
        self.log.debug("volume decreased")

    def onMute(self, event):
        """ミュートの切り替え"""
        if not self.events.mute_status:
            self.parent.menu.SetMenuLabel("FUNCTION_PLAY_MUTE", _("ミュートを解除"))
            self.parent.radio_manager._player.setVolume(0)
            self.parent.radio_manager.volume.Disable()
            self.events.mute_status = True
        else:
            self.parent.menu.SetMenuLabel("FUNCTION_PLAY_MUTE", _("ミュート"))
            self.parent.radio_manager._player.setVolume(self.parent.radio_manager.volume.GetValue())
            self.parent.radio_manager.volume.Enable()
            self.events.mute_status = False

    def changeOutputDevice(self, event):
        """出力デバイスを変更"""
        changeDeviceDialog = changeDevice.ChangeDeviceDialog()
        changeDeviceDialog.Initialize()
        ret = changeDeviceDialog.Show()
        if ret == wx.ID_CANCEL:
            return
        self.parent.radio_manager._player.setDeviceByName(changeDeviceDialog.GetData())
