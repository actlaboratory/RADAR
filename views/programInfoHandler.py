# -*- coding: utf-8 -*-
# 番組情報処理ハンドラーモジュール

import wx
from views import recordingWizzard


class ProgramInfoHandler:
    def __init__(self, parent_view):
        self.parent = parent_view
        self.log = parent_view.log
        self.creator = parent_view.creator
        self.events = parent_view.events
        self._timefree_program_info = None

    def setup_program_info_ui(self):
        """番組情報関連のUIを設定"""
        self.description()
        self.SHOW_NOW_PROGRAMLIST()

    def SHOW_NOW_PROGRAMLIST(self):
        """現在再生中の番組リストを作成"""
        self.nplist, self.nowprograminfo = self.creator.virtualListCtrl(_("現在再生中の番組"), size=(450,160), style=wx.LC_SINGLE_SEL|wx.LC_REPORT|wx.LC_NO_HEADER | wx.BORDER_RAISED, sizerFlag=wx.ALL|wx.EXPAND)
        self.nplist.AppendColumn(_(""),0,180)
        self.nplist.AppendColumn(_(""),0,1500)
        self.nplist.Disable()

    def description(self):
        """番組の説明の表示部分を作る"""
        self.DSCBOX, self.dscboxLabel = self.creator.inputbox(_("説明"), style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_PROCESS_ENTER)
        self.DSCBOX.Disable()

    def get_latest_info(self):
        """ctrl+f5によるリロード処理のときに呼ばれる"""
        if not self.events.displaying:
            return

        if self._timefree_program_info and hasattr(self.parent, "radio_manager"):
            if self.parent.radio_manager.is_timefree_playing():
                self._render_timefree_program_info()
                return

        if hasattr(self.events, 'current_playing_station_id') and self.events.current_playing_station_id:
            self.nplist.clear()
            self.show_program_info(self.events.current_playing_station_id)
            self.show_onair_music(self.events.current_playing_station_id)
            self.show_description(self.events.current_playing_station_id)

    def show_timefree_program_info(self, info):
        """聴き逃し再生中の番組情報を表示"""
        self._timefree_program_info = dict(info or {})
        self._render_timefree_program_info()

    def clear_timefree_program_info(self):
        """聴き逃し番組情報表示を解除"""
        self._timefree_program_info = None

    def _render_timefree_program_info(self):
        if not self.events.displaying:
            return
        info = self._timefree_program_info or {}
        station_name = info.get("station_name", "")
        title = info.get("title", "")
        performer = info.get("performer", "")
        description = info.get("description", "")

        position_sec = 0
        duration_sec = int(max(0, info.get("duration_sec", 0) or 0))
        if hasattr(self.parent, "radio_manager"):
            try:
                position_sec = self.parent.radio_manager.get_timefree_position_seconds()
                duration_sec = self.parent.radio_manager.get_timefree_duration_seconds() or duration_sec
            except Exception:
                pass

        self.nplist.Enable()
        self.nplist.clear()
        self.nplist.Append(("放送局", station_name))
        self.nplist.Append(("番組名", title))
        self.nplist.Append(("出演者", performer))
        if duration_sec > 0:
            self.nplist.Append(("再生位置", f"{self._format_hhmmss(position_sec)} / {self._format_hhmmss(duration_sec)}"))
        else:
            self.nplist.Append(("再生位置", self._format_hhmmss(position_sec)))
        self.nplist.Append(("オンエア曲", ""))

        if description:
            self.DSCBOX.Enable()
            self.DSCBOX.SetValue(description)
        else:
            self.DSCBOX.SetValue("")

    def _format_hhmmss(self, seconds):
        total = int(max(0, seconds))
        hh = total // 3600
        mm = (total % 3600) // 60
        ss = total % 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}"

    def show_description(self, station_id):
        """番組の説明を表示"""
        if not self.events.displaying:
            return
        
        if self.parent.progs.getNowProgramDsc(station_id):
            self.DSCBOX.Enable()
            self.DSCBOX.SetValue(self.parent.progs.getNowProgramDsc(station_id))
        else:
            self.DSCBOX.SetValue("")

    def show_program_info(self, station_id):
        """番組情報を表示"""
        if not self.events.displaying:
            return
        
        self.nplist.Enable()
        program_title = self.parent.progs.getNowProgram(station_id)
        program_pfm = self.parent.progs.getnowProgramPfm(station_id)
        station_name = self.parent.radio_manager.stid.get(station_id, station_id)

        self.nplist.Append(("放送局", station_name))
        self.nplist.Append(("番組名", program_title))
        self.nplist.Append(("出演者", program_pfm))

    def show_onair_music(self, station_id):
        """オンエア曲情報を表示"""
        if not self.events.displaying:
            return
        
        onair_music = self._get_onair_music_safely(station_id)
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
        proglst = recordingWizzard.RecordingWizzard(station_id, self.parent.radio_manager.stid[station_id])
        proglst.Show()
        return

    def switching_programInfo(self, event):
        """番組情報の表示/非表示を切り替え"""
        if self.events.displaying:
            self.parent.menu.SetMenuLabel("HIDE_PROGRAMINFO", _("番組情報を表示(&P)"))
            self.nowprograminfo.Destroy()
            self.nplist.Destroy()
            self.dscboxLabel.Destroy()
            self.DSCBOX.Destroy()
            self.events.displaying = False
            self.creator.GetSizer().Layout()
        else:
            self.parent.menu.SetMenuLabel("HIDE_PROGRAMINFO", _("番組情報の非表示(&H)"))
            self.description()
            self.SHOW_NOW_PROGRAMLIST()
            if hasattr(self.events, 'current_playing_station_id') and self.events.current_playing_station_id:

                self.show_description(self.events.current_playing_station_id)
                self.show_program_info(self.events.current_playing_station_id)
                self.show_onair_music(self.events.current_playing_station_id)
            self.creator.GetSizer().Layout()
            self.events.displaying = True
        
        # 設定をiniファイルに保存
        import globalVars
        globalVars.app.config[self.parent.identifier]["displayProgramInfo"] = self.events.displaying
        globalVars.app.config.write()
            