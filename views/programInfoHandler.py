# -*- coding: utf-8 -*-
# 番組情報処理ハンドラーモジュール

import datetime
import threading

import wx
from views import programmanager
from views import recordingWizzard
import menuItemsStore


class ProgramInfoHandler:
    def __init__(self, parent_view):
        self.parent = parent_view
        self.log = parent_view.log
        self.creator = parent_view.creator
        self.events = parent_view.events
        self._timefree_program_info = None
        self.nplist = None
        self.nowprograminfo = None
        self.DSCBOX = None
        self.dscboxLabel = None
        # 別スレッドでの番組情報の取得ごとに進める。古い取得結果で新しい表示を上書きしないため
        self._info_request = 0
        # 直前に表示したオンエア曲 (放送局ID, 曲)。取得し直す間も同じ局なら表示を残す
        self._last_onair_music = (None, "")

    def setup_program_info_ui(self):
        """番組情報関連のUIを設定"""
        self.ensure_program_info_ui()

    def _is_program_info_ui_alive(self):
        """番組情報UIが生成済みかを返す"""
        return (
            self.nplist is not None and
            self.nowprograminfo is not None and
            self.DSCBOX is not None and
            self.dscboxLabel is not None
        )

    def ensure_program_info_ui(self):
        """番組情報UIが無ければ再生成する"""
        if self._is_program_info_ui_alive():
            return
        self.description()
        self.SHOW_NOW_PROGRAMLIST()

    def _set_program_info_menu_enabled(self, enabled):
        """番組情報表示/非表示メニューの有効状態を更新"""
        try:
            self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("HIDE_PROGRAMINFO"), enabled)
        except Exception:
            pass

    def destroy_program_info_ui(self, disable_menu=False):
        """番組情報UIを破棄する"""
        if self.nowprograminfo is not None:
            self.nowprograminfo.Destroy()
        if self.nplist is not None:
            self.nplist.Destroy()
        if self.dscboxLabel is not None:
            self.dscboxLabel.Destroy()
        if self.DSCBOX is not None:
            self.DSCBOX.Destroy()
        self.nowprograminfo = None
        self.nplist = None
        self.dscboxLabel = None
        self.DSCBOX = None
        if disable_menu:
            self._set_program_info_menu_enabled(False)

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
        self.ensure_program_info_ui()

        if self._timefree_program_info and hasattr(self.parent, "radio_manager"):
            if self.parent.radio_manager.is_timefree_playing():
                self._render_timefree_program_info()
                return

        if hasattr(self.events, 'current_playing_station_id') and self.events.current_playing_station_id:
            self.show_live_program_info(self.events.current_playing_station_id)

    def show_live_program_info(self, station_id):
        """放送中の番組情報を別スレッドで取得して表示する（通信中も画面を止めない）"""
        if not self.events.displaying or not station_id:
            return
        self.ensure_program_info_ui()
        station_name = self.parent.radio_manager.stid.get(station_id, station_id)
        self._load_async(self._fetch_live_program_info, station_id, station_name)

    def _fetch_live_program_info(self, station_id, station_name):
        """（別スレッド）放送中の番組情報とオンエア曲を取得する"""
        # 共有の ProgramManager は取得結果を内部に保持するため、取得ごとに別のものを使う
        progs = programmanager.ProgramManager()
        return {
            "station_id": station_id,
            "station_name": station_name,
            "title": progs.getNowProgram(station_id) or "",
            "performer": progs.getnowProgramPfm(station_id) or "",
            "description": progs.getNowProgramDsc(station_id) or "",
            "onair_music": self._get_onair_music_safely(progs, station_id) or "",
        }

    def _load_async(self, fetch, *args):
        """fetch(*args) を別スレッドで実行し、結果の番組情報を UI スレッドで表示する"""
        self._info_request += 1
        request = self._info_request

        def run():
            try:
                info = fetch(*args)
            except Exception as e:
                self.log.warning(f"Failed to load program info: {e!r}")
                return
            wx.CallAfter(self._apply_loaded_info, request, info)

        threading.Thread(target=run, name="ProgramInfo", daemon=True).start()

    def _apply_loaded_info(self, request, info):
        """（UI スレッド）取得した番組情報を表示する。より新しい取得・表示があれば何もしない"""
        if request != self._info_request:
            return
        if not self.events.displaying or not self._is_program_info_ui_alive():
            return
        self._fill_program_details(info)

    def show_timefree_program_info(self, info):
        """聴き逃し再生中の番組情報を表示"""
        self._timefree_program_info = dict(info or {})
        self._render_timefree_program_info()

    def show_program_info_snapshot(self, info):
        """ダイアログ等で選択した番組情報を表示（ライブ再生開始時）"""
        if not self.events.displaying:
            return
        self.ensure_program_info_ui()
        snapshot = dict(info or {})
        if not snapshot.get("station_id"):
            snapshot["station_id"] = getattr(self.events, "current_playing_station_id", None)
        self._render_program_details(snapshot, include_onair_music=True)

    def clear_timefree_program_info(self):
        """聴き逃し番組情報表示を解除"""
        self._timefree_program_info = None

    def _render_program_details(self, info, *, include_onair_music=False):
        """番組情報を表示する。オンエア曲は別スレッドで取得し、取得できたら表示し直す"""
        info = dict(info)
        if not info.get("station_name") and info.get("station_id") and hasattr(self.parent, "radio_manager"):
            info["station_name"] = self.parent.radio_manager.stid.get(info["station_id"], info["station_id"])
        if not include_onair_music:
            # 取得中の結果で、この表示を上書きしないようにする
            self._info_request += 1
            self._fill_program_details(info)
            return
        station_id = info.get("station_id") or getattr(self.events, "current_playing_station_id", None)
        last_station_id, last_music = self._last_onair_music
        info["onair_music"] = last_music if station_id and station_id == last_station_id else ""
        self._fill_program_details(info)
        if not station_id:
            self._info_request += 1
            return
        target_dt = None
        use_timefree = self._should_use_timefree_onair_music(info)
        if use_timefree:
            target_dt = self._get_timefree_onair_target_dt(info)
        self._load_async(self._fetch_onair_music_for_details, info, station_id, use_timefree, target_dt)

    def _fetch_onair_music_for_details(self, info, station_id, use_timefree, target_dt):
        """（別スレッド）番組情報にオンエア曲を加えて返す"""
        progs = programmanager.ProgramManager()
        if use_timefree:
            music = self._get_onair_music_at_safely(progs, station_id, target_dt)
        else:
            music = self._get_onair_music_safely(progs, station_id)
        return dict(info, onair_music=music or "")

    def _fill_program_details(self, info):
        """（UI スレッド）番組情報の一覧と説明を書き換える。onair_music があればオンエア曲の行も出す"""
        station_name = info.get("station_name", "")
        title = info.get("title", "")
        performer = info.get("performer", "")
        description = info.get("description", "")

        self.nplist.Enable()
        self.nplist.clear()
        self.nplist.Append(("放送局", station_name))
        self.nplist.Append(("番組名", title))
        self.nplist.Append(("出演者", performer))
        if "onair_music" in info:
            self.nplist.Append(("オンエア曲", info["onair_music"]))
            self._last_onair_music = (info.get("station_id"), info["onair_music"])

        if description:
            self.DSCBOX.Enable()
            self.DSCBOX.SetValue(description)
        else:
            self.DSCBOX.SetValue("")

    def _render_timefree_program_info(self):
        if not self.events.displaying:
            return
        self.ensure_program_info_ui()
        self._render_program_details(self._timefree_program_info or {}, include_onair_music=True)

    def _should_use_timefree_onair_music(self, info):
        if hasattr(self.parent, "radio_manager") and self.parent.radio_manager.is_timefree_playing():
            return True
        return bool(info.get("ft_dt") or info.get("to_dt"))

    def _get_timefree_onair_target_dt(self, info):
        ft_dt = info.get("ft_dt")
        if ft_dt is None:
            start_time = info.get("start_time", "")
            try:
                ft_dt = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                return None

        position_sec = 0
        if hasattr(self.parent, "radio_manager"):
            try:
                position_sec = self.parent.radio_manager.get_timefree_position_seconds()
            except Exception:
                pass
        return ft_dt + datetime.timedelta(seconds=position_sec)

    def _get_onair_music_at_safely(self, progs, station_id, target_dt):
        try:
            return progs.get_onair_music_at(station_id, target_dt=target_dt)
        except Exception as e:
            self.log.warning(f"Failed to get onair music at {target_dt}: {e}")
            return None

    def _get_onair_music_safely(self, progs, station_id):
        """オンエア曲情報を取得"""
        try:
            return progs.get_onair_music(station_id)
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
            self.destroy_program_info_ui()
            self.events.displaying = False
            self.creator.GetSizer().Layout()
        else:
            self.parent.menu.SetMenuLabel("HIDE_PROGRAMINFO", _("番組情報の非表示(&H)"))
            self.ensure_program_info_ui()
            self._set_program_info_menu_enabled(True)
            self.events.displaying = True
            self.get_latest_info()
            self.creator.GetSizer().Layout()
        
        # 設定をiniファイルに保存
        import globalVars
        globalVars.app.config[self.parent.identifier]["displayProgramInfo"] = self.events.displaying
        globalVars.app.config.write()
            