# -*- coding: utf-8 -*-
# recording manager dialog
# 録音管理・予約録音管理ダイアログ（タブ統合）

import wx
import datetime
from logging import getLogger
from views.baseDialog import *
from recorder import (
    recorder_manager,
    schedule_manager,
    RECORDING_STATUS_SCHEDULED,
    RECORDING_STATUS_RECORDING,
    RECORDING_STATUS_COMPLETED,
    RECORDING_STATUS_CANCELLED,
    RECORDING_STATUS_FAILED,
)
from notification_util import notify as notification_notify


class RecordingManagerDialog(BaseDialog):
    """録音管理・予約録音管理ダイアログ"""

    def __init__(self, initial_tab="recordings"):
        super().__init__("RecordingManagerDialog")
        self.log = getLogger("recording_manager")
        self.recorder_manager = recorder_manager
        self.initial_tab = initial_tab
        self.active_recorders = []
        self.schedules = []
        self.timer = None
        self.selected_active_index = -1
        self.selected_schedule_index = -1
        self.selected_schedule_id = None

    def Initialize(self):
        """ダイアログを初期化"""
        self.log.debug("created")
        super().Initialize(globalVars.app.hMainView.hFrame, _("録音管理"))
        self.InstallControls()
        self.load_all_data()
        self.wnd.SetEscapeId(wx.ID_CANCEL)

        # 自動更新タイマーを開始（5秒ごと）
        self.timer = wx.Timer()
        self.timer.Bind(wx.EVT_TIMER, self.onRefresh)
        self.timer.Start(5000)
        return True

    def InstallControls(self):
        """コントロールを配置"""
        self.notebook = wx.Notebook(self.panel)
        self.sizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 10)

        self._create_recordings_tab()
        self._create_schedules_tab()

        # 統一の閉じるボタン
        bottom_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.sizer.Add(bottom_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        bottom_sizer.AddStretchSpacer()
        self.close_btn = wx.Button(self.panel, wx.ID_CANCEL, _("閉じる(&X)"))
        self.close_btn.SetDefault()
        bottom_sizer.Add(self.close_btn, 0)

        self.close_btn.Bind(wx.EVT_BUTTON, self.onClose)
        self.active_lst.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onActiveListSelected)
        self.schedule_lst.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onScheduleListSelected)

        if self.initial_tab == "schedules":
            self.notebook.SetSelection(1)
        else:
            self.notebook.SetSelection(0)

    def _create_recordings_tab(self):
        tab = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)
        tab.SetSizer(sizer)

        label = wx.StaticText(tab, label=_("現在の録音一覧"))
        sizer.Add(label, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        self.active_lst = wx.ListCtrl(tab, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self.active_lst.InsertColumn(0, _("放送局"), width=200)
        self.active_lst.InsertColumn(1, _("番組名"), width=330)
        self.active_lst.InsertColumn(2, _("開始時刻"), width=120)
        self.active_lst.InsertColumn(3, _("状態"), width=120)
        self.active_lst.InsertColumn(4, _("ファイル名"), width=420)
        sizer.Add(self.active_lst, 1, wx.EXPAND | wx.ALL, 10)

        completed_label = wx.StaticText(tab, label=_("完了した録音"))
        sizer.Add(completed_label, 0, wx.LEFT | wx.RIGHT, 10)

        self.completed_lst = wx.ListCtrl(tab, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self.completed_lst.InsertColumn(0, _("放送局"), width=200)
        self.completed_lst.InsertColumn(1, _("番組名"), width=330)
        self.completed_lst.InsertColumn(2, _("開始時刻"), width=150)
        self.completed_lst.InsertColumn(3, _("終了時刻"), width=150)
        self.completed_lst.InsertColumn(4, _("ファイル名"), width=360)
        sizer.Add(self.completed_lst, 1, wx.EXPAND | wx.ALL, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.refresh_btn = wx.Button(tab, wx.ID_ANY, _("更新(&R)"))
        self.stop_btn = wx.Button(tab, wx.ID_ANY, _("選択した録音を停止(&S)"))
        self.stop_all_btn = wx.Button(tab, wx.ID_ANY, _("全て停止(&A)"))
        btn_sizer.Add(self.refresh_btn, 0, wx.RIGHT, 8)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(self.stop_btn, 0, wx.RIGHT, 8)
        btn_sizer.Add(self.stop_all_btn, 0)

        self.refresh_btn.Bind(wx.EVT_BUTTON, self.onRefresh)
        self.stop_btn.Bind(wx.EVT_BUTTON, self.onStop)
        self.stop_all_btn.Bind(wx.EVT_BUTTON, self.onStopAll)

        self.notebook.AddPage(tab, _("録音管理"))

    def _create_schedules_tab(self):
        tab = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)
        tab.SetSizer(sizer)

        label = wx.StaticText(tab, label=_("スケジュール録音一覧"))
        sizer.Add(label, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        self.schedule_lst = wx.ListCtrl(tab, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self.schedule_lst.InsertColumn(0, _("番組タイトル"), width=280)
        self.schedule_lst.InsertColumn(1, _("放送局"), width=180)
        self.schedule_lst.InsertColumn(2, _("開始時間"), width=140)
        self.schedule_lst.InsertColumn(3, _("終了時間"), width=140)
        self.schedule_lst.InsertColumn(4, _("ステータス"), width=120)
        self.schedule_lst.InsertColumn(5, _("出力パス"), width=380)
        sizer.Add(self.schedule_lst, 1, wx.EXPAND | wx.ALL, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.schedule_refresh_btn = wx.Button(tab, wx.ID_ANY, _("更新(&R)"))
        self.schedule_cancel_btn = wx.Button(tab, wx.ID_ANY, _("キャンセル(&C)"))
        self.schedule_remove_btn = wx.Button(tab, wx.ID_ANY, _("削除(&D)"))
        self.schedule_clear_all_btn = wx.Button(tab, wx.ID_ANY, _("すべて削除(&A)"))
        btn_sizer.Add(self.schedule_refresh_btn, 0, wx.RIGHT, 8)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(self.schedule_cancel_btn, 0, wx.RIGHT, 8)
        btn_sizer.Add(self.schedule_remove_btn, 0, wx.RIGHT, 8)
        btn_sizer.Add(self.schedule_clear_all_btn, 0)

        self.schedule_refresh_btn.Bind(wx.EVT_BUTTON, self.onRefresh)
        self.schedule_cancel_btn.Bind(wx.EVT_BUTTON, self.onScheduleCancel)
        self.schedule_remove_btn.Bind(wx.EVT_BUTTON, self.onScheduleRemove)
        self.schedule_clear_all_btn.Bind(wx.EVT_BUTTON, self.onScheduleClearAll)

        self.notebook.AddPage(tab, _("予約録音管理"))

    def onActiveListSelected(self, event):
        self.selected_active_index = event.GetIndex()
        event.Skip()

    def onScheduleListSelected(self, event):
        self.selected_schedule_index = event.GetIndex()
        if 0 <= self.selected_schedule_index < len(self.schedules):
            self.selected_schedule_id = self.schedules[self.selected_schedule_index].id
        event.Skip()

    def _set_list_row(self, list_ctrl, row_idx, values):
        list_ctrl.InsertItem(row_idx, str(values[0]))
        for col, value in enumerate(values[1:], start=1):
            list_ctrl.SetItem(row_idx, col, str(value))

    def _get_selected_active_index(self):
        selected = self.active_lst.GetFirstSelected()
        if selected >= 0:
            self.selected_active_index = selected
            return selected
        if 0 <= self.selected_active_index < len(self.active_recorders):
            return self.selected_active_index
        return -1

    def load_all_data(self):
        self.load_recordings()
        self.load_schedules()

    def load_recordings(self):
        """録音一覧を読み込み"""
        try:
            self.active_recorders = self.recorder_manager.get_active_recorders()
            self._update_active_list()
            self._load_completed_recordings()
        except Exception as e:
            self.log.error(f"Failed to load recordings: {e}")

    def _update_active_list(self):
        self.active_lst.DeleteAllItems()

        for idx, (recorder, info) in enumerate(self.active_recorders):
            parts = info.split(' ', 1)
            station_name = parts[0] if parts else "不明"
            program_title = parts[1] if len(parts) > 1 else "不明"
            file_name = f"{recorder.output_path}.{recorder.filetype}".split("\\")[-1]
            start_time = datetime.datetime.now().strftime("%H:%M:%S")
            self._set_list_row(
                self.active_lst,
                idx,
                (station_name, program_title, start_time, _("録音中"), file_name),
            )

        # フォーカス移動後も停止できるよう、前回選択を可能な範囲で復元
        if self.active_recorders:
            if not (0 <= self.selected_active_index < len(self.active_recorders)):
                self.selected_active_index = 0
            self.active_lst.Select(self.selected_active_index)
            self.active_lst.Focus(self.selected_active_index)
        else:
            self.selected_active_index = -1

    def _load_completed_recordings(self):
        self.completed_lst.DeleteAllItems()
        schedules = schedule_manager.get_schedules()
        completed = [s for s in schedules if s.status == RECORDING_STATUS_COMPLETED]
        for idx, schedule in enumerate(completed):
            start_time_str = schedule.start_time.strftime("%Y-%m-%d %H:%M")
            end_time_str = schedule.end_time.strftime("%Y-%m-%d %H:%M")
            file_name = f"{schedule.output_path}.{schedule.filetype}".split("\\")[-1]
            self._set_list_row(
                self.completed_lst,
                idx,
                (schedule.station_name, schedule.program_title, start_time_str, end_time_str, file_name),
            )

    def load_schedules(self):
        """予約一覧を読み込み"""
        try:
            all_schedules = schedule_manager.get_schedules()
            self.schedules = [s for s in all_schedules if s.status != RECORDING_STATUS_COMPLETED]
            self._update_schedule_list()
        except Exception as e:
            self.log.error(f"Failed to load schedules: {e}")

    def _update_schedule_list(self):
        self.schedule_lst.DeleteAllItems()
        for idx, schedule in enumerate(self.schedules):
            start_time_str = schedule.start_time.strftime("%Y-%m-%d %H:%M")
            end_time_str = schedule.end_time.strftime("%Y-%m-%d %H:%M")
            self._set_list_row(
                self.schedule_lst,
                idx,
                (
                    schedule.program_title,
                    schedule.station_name,
                    start_time_str,
                    end_time_str,
                    schedule.get_status_display_name(),
                    schedule.output_path,
                ),
            )

        # フォーカス移動や自動更新後も選択状態を維持
        if self.schedules:
            restore_index = -1
            if self.selected_schedule_id:
                for i, schedule in enumerate(self.schedules):
                    if schedule.id == self.selected_schedule_id:
                        restore_index = i
                        break
            if restore_index < 0 and 0 <= self.selected_schedule_index < len(self.schedules):
                restore_index = self.selected_schedule_index
            if restore_index < 0:
                restore_index = 0

            self.selected_schedule_index = restore_index
            self.selected_schedule_id = self.schedules[restore_index].id
            self.schedule_lst.Select(restore_index)
            self.schedule_lst.Focus(restore_index)
        else:
            self.selected_schedule_index = -1
            self.selected_schedule_id = None

    def onRefresh(self, event):
        """リストを更新"""
        self.load_all_data()

    def onStop(self, event):
        """選択された録音を停止"""
        try:
            selected = self._get_selected_active_index()
            if selected < 0:
                wx.MessageBox(_("録音を選択してください。"), _("エラー"), wx.OK | wx.ICON_ERROR)
                return
            if selected >= len(self.active_recorders):
                wx.MessageBox(_("選択された録音が見つかりません。"), _("エラー"), wx.OK | wx.ICON_ERROR)
                return

            recorder, info = self.active_recorders[selected]
            result = wx.MessageBox(
                f"'{info}' の録音を停止しますか？",
                _("確認"),
                wx.YES_NO | wx.ICON_QUESTION
            )
            if result == wx.YES:
                self.recorder_manager.stop_recorder(recorder)
                notification_notify(
                    title='録音停止',
                    message=f'{info} の録音を停止しました。',
                    app_name='rpb',
                    timeout=10
                )
                self.load_recordings()
        except Exception as e:
            self.log.error(f"Error stopping recording: {e}")
            wx.MessageBox(f"録音の停止に失敗しました: {e}", _("エラー"), wx.OK | wx.ICON_ERROR)

    def onStopAll(self, event):
        """全ての録音を停止"""
        try:
            if not self.active_recorders:
                wx.MessageBox(_("停止する録音がありません。"), _("情報"), wx.OK | wx.ICON_INFORMATION)
                return
            result = wx.MessageBox(
                f"全ての録音（{len(self.active_recorders)}件）を停止しますか？",
                _("確認"),
                wx.YES_NO | wx.ICON_QUESTION
            )
            if result == wx.YES:
                self.recorder_manager.stop_all()
                notification_notify(
                    title='録音停止',
                    message='全ての録音を停止しました。',
                    app_name='rpb',
                    timeout=10
                )
                self.load_recordings()
        except Exception as e:
            self.log.error(f"Error stopping all recordings: {e}")
            wx.MessageBox(f"録音の停止に失敗しました: {e}", _("エラー"), wx.OK | wx.ICON_ERROR)

    def _get_selected_schedule(self):
        selected = self.schedule_lst.GetFirstSelected()
        if selected >= 0 and selected < len(self.schedules):
            self.selected_schedule_index = selected
            self.selected_schedule_id = self.schedules[selected].id
            return self.schedules[selected]

        # 選択表示が外れていても、直前選択をフォールバック
        if self.selected_schedule_id:
            for i, schedule in enumerate(self.schedules):
                if schedule.id == self.selected_schedule_id:
                    self.selected_schedule_index = i
                    return schedule
        if 0 <= self.selected_schedule_index < len(self.schedules):
            schedule = self.schedules[self.selected_schedule_index]
            self.selected_schedule_id = schedule.id
            return schedule
        return None

    def onScheduleCancel(self, event):
        """選択された予約をキャンセル"""
        try:
            schedule = self._get_selected_schedule()
            if schedule is None:
                wx.MessageBox(_("予約を選択してください。"), _("エラー"), wx.OK | wx.ICON_ERROR)
                return
            if schedule.status in [RECORDING_STATUS_COMPLETED, RECORDING_STATUS_CANCELLED, RECORDING_STATUS_FAILED]:
                wx.MessageBox(_("この予約は既に完了またはキャンセル済みです。"), _("エラー"), wx.OK | wx.ICON_ERROR)
                return
            result = wx.MessageBox(
                f"'{schedule.program_title}' の録音予約をキャンセルしますか？",
                _("確認"),
                wx.YES_NO | wx.ICON_QUESTION
            )
            if result == wx.YES:
                schedule_manager.cancel_schedule(schedule.id)
                self.load_schedules()
                wx.MessageBox(_("予約をキャンセルしました。"), _("完了"), wx.OK | wx.ICON_INFORMATION)
        except Exception as e:
            self.log.error(f"Error cancelling schedule: {e}")
            wx.MessageBox(f"キャンセルに失敗しました: {e}", _("エラー"), wx.OK | wx.ICON_ERROR)

    def onScheduleRemove(self, event):
        """選択された予約を削除"""
        try:
            schedule = self._get_selected_schedule()
            if schedule is None:
                wx.MessageBox(_("予約を選択してください。"), _("エラー"), wx.OK | wx.ICON_ERROR)
                return
            result = wx.MessageBox(
                f"'{schedule.program_title}' の録音予約を削除しますか？\n（この操作は取り消せません）",
                _("確認"),
                wx.YES_NO | wx.ICON_WARNING
            )
            if result == wx.YES:
                schedule_manager.remove_schedule(schedule.id)
                self.load_schedules()
                wx.MessageBox(_("予約を削除しました。"), _("完了"), wx.OK | wx.ICON_INFORMATION)
        except Exception as e:
            self.log.error(f"Error removing schedule: {e}")
            wx.MessageBox(f"削除に失敗しました: {e}", _("エラー"), wx.OK | wx.ICON_ERROR)

    def onScheduleClearAll(self, event):
        """すべての予約を削除"""
        try:
            if not self.schedules:
                wx.MessageBox(_("削除する予約がありません。"), _("情報"), wx.OK | wx.ICON_INFORMATION)
                return
            result = wx.MessageBox(
                f"すべての録音予約（{len(self.schedules)}件）を削除しますか？\n"
                "録音中の予約はキャンセルされます。\n"
                "（この操作は取り消せません）",
                _("確認"),
                wx.YES_NO | wx.ICON_WARNING
            )
            if result == wx.YES:
                removed_count = schedule_manager.clear_all_schedules()
                self.load_schedules()
                wx.MessageBox(
                    f"すべての予約を削除しました。\n（{removed_count}件の予約を削除）",
                    _("完了"),
                    wx.OK | wx.ICON_INFORMATION
                )
        except Exception as e:
            self.log.error(f"Error clearing all schedules: {e}")
            wx.MessageBox(f"すべて削除に失敗しました: {e}", _("エラー"), wx.OK | wx.ICON_ERROR)

    def onClose(self, event):
        """ダイアログを閉じる"""
        if self.timer:
            self.timer.Stop()
        if self.wnd and self.wnd.IsModal():
            self.wnd.EndModal(wx.ID_CANCEL)
        elif self.wnd:
            self.wnd.Destroy()