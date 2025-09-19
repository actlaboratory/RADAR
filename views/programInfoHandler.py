# -*- coding: utf-8 -*-
# 番組情報処理ハンドラーモジュール

import wx
from views import showRadioProgramScheduleListBase
from simpleDialog import *


class ProgramInfoHandler:
    def __init__(self, parent_view):
        self.parent = parent_view
        self.log = parent_view.log
        self.creator = parent_view.creator
        self.events = parent_view.events

    def setup_program_info_ui(self):
        """番組情報関連のUIを設定"""
        self.description()
        self.SHOW_NOW_PROGRAMLIST()

    def SHOW_NOW_PROGRAMLIST(self):
        """現在再生中の番組リストを作成"""
        self.nplist, nowprograminfo = self.creator.virtualListCtrl(_("現在再生中の番組"))
        self.nplist.AppendColumn(_("現在再生中"))
        self.nplist.AppendColumn(_(""))


    def description(self):
        """番組の説明の表示部分を作る"""
        # 番組の説明の表示部分をつくる
        self.DSCBOX, label = self.creator.inputbox(_("説明"), style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_PROCESS_ENTER)  # 読み取り専用のテキストボックス
        self.DSCBOX.Disable()  # 初期状態は無効

    def get_latest_info(self):
        """ctrl+f5によるリロード処理のときに呼ばれる"""
        if hasattr(self.events, 'current_playing_station_id') and self.events.current_playing_station_id:
            self.nplist.clear()
            self.show_program_info(self.events.current_playing_station_id)
            self.show_onair_music(self.events.current_playing_station_id)
            self.show_description(self.events.current_playing_station_id)

    def show_description(self, station_id):
        """番組の説明を表示"""
        if self.parent.progs.getNowProgramDsc(station_id):
            self.DSCBOX.Enable()
            self.DSCBOX.SetValue(self.parent.progs.getNowProgramDsc(station_id))
        else:
            self.DSCBOX.SetValue("")

    def show_program_info(self, station_id):
        """番組情報を表示"""
        program_title = self.parent.progs.getNowProgram(station_id)
        program_pfm = self.parent.progs.getnowProgramPfm(station_id)
        station_name = self.parent.radio_manager.stid.get(station_id, station_id)

        # リストビューにアペンド
        self.nplist.Append(("放送局", station_name))
        self.nplist.Append(("番組名", program_title))
        self.nplist.Append(("出演者", program_pfm))

    def show_onair_music(self, station_id):
        """オンエア曲情報を表示"""
        onair_music = self._get_onair_music_safely(station_id)
        print(onair_music)
        if onair_music:
            self.nplist.Append(("オンエア曲", onair_music))
        else:
            self.nplist.Append(("オンエア曲", ""))

    def _get_onair_music_safely(self, station_id):
        """オンエア曲情報を取得"""
        try:
            return self.parent.progs.get_onair_music(station_id)
        except Exception as e:
            self.log.warning(f"Failed to get online music: {e}")
            return None

    def initializeInfoView(self, station_id):
        """番組一覧表示"""
        proglst = showRadioProgramScheduleListBase.ShowSchedule(station_id, self.parent.radio_manager.stid[station_id])
        proglst.Initialize()
        proglst.Show()
        return

    def switching_programInfo(self, event):
        """番組情報の表示/非表示を切り替え"""
        if self.events.displaying:
            self.parent.menu.SetMenuLabel("HIDE_PROGRAMINFO", _("番組情報を表示&P"))
            self.nplist.Destroy()
            self.DSCBOX.Destroy()
            self.events.displaying = False
        else:
            self.parent.menu.SetMenuLabel("HIDE_PROGRAMINFO", _("番組情報の非表示&H"))
            self.description()
            self.SHOW_NOW_PROGRAMLIST()
            if hasattr(self.events, 'current_playing_station_id') and self.events.current_playing_station_id:

                self.show_description(self.events.current_playing_station_id)
                self.show_program_info(self.events.current_playing_station_id)
                self.show_onair_music(self.events.current_playing_station_id)
            self.events.displaying = True
            