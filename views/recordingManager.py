# -*- coding: utf-8 -*-
# recording manager dialog
# 録音管理・予約録音管理ダイアログ（タブ統合）

import wx
import datetime
from logging import getLogger
from views.baseDialog import *
import views.ViewCreator
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
import simpleDialog


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

        # 自動更新タイマーを開始（5秒ごと）
        self.timer = wx.Timer()
        self.timer.Bind(wx.EVT_TIMER, self.onRefresh)
        self.timer.Start(5000)
        return True

    def InstallControls(self):
        """コントロールを配置"""
        self.creator = views.ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.VERTICAL, 10, style=wx.EXPAND|wx.ALL, margin=10)
        self.notebook = self.creator.tabCtrl(_("録音管理"), sizerFlag=wx.EXPAND|wx.ALL, proportion=1)

        self._create_recordings_tab()
        self._create_schedules_tab()

        bottom_creator = views.ViewCreator.ViewCreator(self.viewMode, self.creator.GetPanel(), self.creator.GetSizer(), wx.HORIZONTAL, style=wx.ALIGN_RIGHT|wx.ALL, margin=10)
        self.close_btn = bottom_creator.closebutton(_("閉じる(&X)"), self.onClose)
        self.close_btn.SetDefault()

        self.active_lst.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onActiveListSelected)
        self.schedule_lst.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onScheduleListSelected)

        if self.initial_tab == "schedules":
            self.notebook.SetSelection(1)
        else:
            self.notebook.SetSelection(0)

    def _create_recordings_tab(self):
        tab = views.ViewCreator.ViewCreator(self.viewMode, self.notebook, None, wx.VERTICAL, 10, label=_("録音管理"), style=wx.EXPAND|wx.ALL, margin=10)

        self.active_lst, _lbl = tab.listCtrl(_("現在の録音一覧"), size=(800, 200), sizerFlag=wx.ALL|wx.EXPAND)
        self.active_lst.InsertColumn(0, _("放送局"), width=200)
        self.active_lst.InsertColumn(1, _("番組名"), width=330)
        self.active_lst.InsertColumn(2, _("開始時刻"), width=120)
        self.active_lst.InsertColumn(3, _("状態"), width=120)
        self.active_lst.InsertColumn(4, _("ファイル名"), width=420)

        self.completed_lst, _lbl = tab.listCtrl(_("完了した録音"), size=(800, 200), sizerFlag=wx.ALL|wx.EXPAND)
        self.completed_lst.InsertColumn(0, _("放送局"), width=200)
        self.completed_lst.InsertColumn(1, _("番組名"), width=330)
        self.completed_lst.InsertColumn(2, _("開始時刻"), width=150)
        self.completed_lst.InsertColumn(3, _("終了時刻"), width=150)
        self.completed_lst.InsertColumn(4, _("ファイル名"), width=360)

        btn_creator = views.ViewCreator.ViewCreator(self.viewMode, tab.GetPanel(), tab.GetSizer(), wx.HORIZONTAL, style=wx.ALL, margin=10)
        self.refresh_btn = btn_creator.button(_("更新(&R)"), self.onRefresh)
        btn_creator.AddSpace(-1)
        self.stop_btn = btn_creator.button(_("選択した録音を停止(&S)"), self.onStop)
        self.stop_all_btn = btn_creator.button(_("全て停止(&A)"), self.onStopAll)

    def _create_schedules_tab(self):
        tab = views.ViewCreator.ViewCreator(self.viewMode, self.notebook, None, wx.VERTICAL, 10, label=_("予約録音管理"), style=wx.EXPAND|wx.ALL, margin=10, proportion=1)

        self.schedule_lst, _lbl = tab.listCtrl(_("スケジュール録音一覧"), size=(800, 300), sizerFlag=wx.ALL|wx.EXPAND, proportion=1)
        self.schedule_lst.InsertColumn(0, _("番組タイトル"), width=280)
        self.schedule_lst.InsertColumn(1, _("放送局"), width=180)
        self.schedule_lst.InsertColumn(2, _("開始時間"), width=140)
        self.schedule_lst.InsertColumn(3, _("終了時間"), width=140)
        self.schedule_lst.InsertColumn(4, _("ステータス"), width=120)
        self.schedule_lst.InsertColumn(5, _("出力パス"), width=380)

        tab.staticText(
            _(
                "「予約を取り消す」は、予約済み・録音中の予約をキャンセル済みにします。"
                "開始時刻までは「予約を復活」で予約済みに戻せます。\n"
                "キャンセル済み・失敗の行では同じボタンで一覧から削除します。"
            ),
            sizerFlag=wx.ALL | wx.EXPAND,
            margin=10,
        )

        btn_creator = views.ViewCreator.ViewCreator(self.viewMode, tab.GetPanel(), tab.GetSizer(), wx.HORIZONTAL, style=wx.ALL, margin=10)
        self.schedule_refresh_btn = btn_creator.button(_("更新(&R)"), self.onRefresh)
        btn_creator.AddSpace(-1)
        self.schedule_revoke_btn = btn_creator.button(_("予約を取り消す(&C)"), self.onScheduleRevoke)
        self.schedule_reactivate_btn = btn_creator.button(_("予約を復活(&U)"), self.onScheduleReactivate)
        self.schedule_clear_all_btn = btn_creator.button(_("すべて一覧から削除(&A)"), self.onScheduleClearAll)

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
                simpleDialog.errorDialog(_("録音を選択してください。"), self.wnd)
                return
            if selected >= len(self.active_recorders):
                simpleDialog.errorDialog(_("選択された録音が見つかりません。"), self.wnd)
                return

            recorder, info = self.active_recorders[selected]
            result = simpleDialog.yesNoDialog(
                _("確認"),
                f"'{info}' の録音を停止しますか？",
                self.wnd,
            )
            if result == wx.ID_YES:
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
            simpleDialog.errorDialog(_("録音の停止に失敗しました。") + f"\n{e}", self.wnd)

    def onStopAll(self, event):
        """全ての録音を停止"""
        try:
            if not self.active_recorders:
                simpleDialog.dialog(_("情報"), _("停止する録音がありません。"), self.wnd)
                return
            result = simpleDialog.yesNoDialog(
                _("確認"),
                f"全ての録音（{len(self.active_recorders)}件）を停止しますか？",
                self.wnd,
            )
            if result == wx.ID_YES:
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
            simpleDialog.errorDialog(_("録音の停止に失敗しました。") + f"\n{e}", self.wnd)

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

    def onScheduleRevoke(self, event):
        """予約済み・録音中はキャンセル済みにする。キャンセル済み・失敗なら一覧から削除する。"""
        try:
            schedule = self._get_selected_schedule()
            if schedule is None:
                simpleDialog.errorDialog(_("予約を選択してください。"), self.wnd)
                return

            st = schedule.status
            if st in (RECORDING_STATUS_SCHEDULED, RECORDING_STATUS_RECORDING):
                if st == RECORDING_STATUS_RECORDING:
                    confirm = (
                        f"'{schedule.program_title}' の録音を停止し、この予約をキャンセル済みにしますか？\n\n"
                        + _("開始時刻前であれば、のちほど「予約を復活」で予約済みに戻せます。")
                    )
                else:
                    confirm = (
                        f"'{schedule.program_title}' の録音予約を取り消しますか？\n\n"
                        + _("開始時刻までは「予約を復活」から取り消しを取りやめられます。")
                    )
                result = simpleDialog.yesNoDialog(_("確認"), confirm, self.wnd)
                if result != wx.ID_YES:
                    return
                if not schedule_manager.cancel_schedule(schedule.id):
                    simpleDialog.errorDialog(_("予約の取り消しに失敗しました。"), self.wnd)
                    return
                self.load_all_data()
                simpleDialog.dialog(_("完了"), _("予約を取り消しました。"), self.wnd)

            elif st in (RECORDING_STATUS_CANCELLED, RECORDING_STATUS_FAILED):
                result = simpleDialog.yesNoDialog(
                    _("確認"),
                    f"'{schedule.program_title}' を一覧から削除しますか？\n\n"
                    + _("予約データから行が取り除かれます。この操作は取り消せません。"),
                    self.wnd,
                )
                if result != wx.ID_YES:
                    return
                schedule_manager.remove_schedule(schedule.id)
                self.load_all_data()
                simpleDialog.dialog(_("完了"), _("一覧から削除しました。"), self.wnd)
            else:
                simpleDialog.errorDialog(_("この予約はこの操作では処理できません。"), self.wnd)
        except Exception as e:
            self.log.error(f"Error in schedule revoke/remove: {e}")
            simpleDialog.errorDialog(_("予約の処理に失敗しました。") + f"\n{e}", self.wnd)

    def onScheduleReactivate(self, event):
        """キャンセル済み・開始時刻前の予約を予約済みに戻す"""
        try:
            schedule = self._get_selected_schedule()
            if schedule is None:
                simpleDialog.errorDialog(_("予約を選択してください。"), self.wnd)
                return
            if schedule.status != RECORDING_STATUS_CANCELLED:
                simpleDialog.errorDialog(_("キャンセル済みの予約だけ復活できます。"), self.wnd)
                return
            if datetime.datetime.now() >= schedule.start_time:
                simpleDialog.errorDialog(_("開始時刻を過ぎたため、復活できません。"), self.wnd)
                return

            result = simpleDialog.yesNoDialog(
                _("確認"),
                f"'{schedule.program_title}' を予約済みに戻しますか？",
                self.wnd,
            )
            if result != wx.ID_YES:
                return

            err = schedule_manager.reactivate_schedule(schedule.id)
            if err is None:
                self.load_all_data()
                simpleDialog.dialog(_("完了"), _("予約を復活し、予約済みに戻しました。"), self.wnd)
                return

            err_messages = {
                "not_found": _("対象の予約が見つかりません。"),
                "not_cancelled": _("キャンセル済みの予約だけ復活できます。"),
                "too_late": _("開始時刻を過ぎたため、復活できません。"),
            }
            simpleDialog.errorDialog(err_messages.get(err, _("復活に失敗しました。")), self.wnd)
        except Exception as e:
            self.log.error(f"Error reactivating schedule: {e}")
            simpleDialog.errorDialog(_("復活に失敗しました。") + f"\n{e}", self.wnd)

    def onScheduleClearAll(self, event):
        """すべての予約を削除"""
        try:
            if not self.schedules:
                simpleDialog.dialog(_("情報"), _("削除する予約がありません。"), self.wnd)
                return
            result = simpleDialog.yesNoDialog(
                _("確認"),
                f"すべての録音予約（{len(self.schedules)}件）を一覧から削除しますか？\n"
                "録音中の予約は先に中止され、その後すべての行がデータから取り除かれます。\n"
                "（この操作は取り消せません）",
                self.wnd,
            )
            if result == wx.ID_YES:
                removed_count = schedule_manager.clear_all_schedules()
                self.load_schedules()
                simpleDialog.dialog(_("完了"), f"すべて一覧から削除しました。\n（{removed_count}件）", self.wnd)
        except Exception as e:
            self.log.error(f"Error clearing all schedules: {e}")
            simpleDialog.errorDialog(_("すべて削除に失敗しました。") + f"\n{e}", self.wnd)

    def onClose(self, event):
        """ダイアログを閉じる"""
        if self.timer:
            self.timer.Stop()
        if self.wnd and self.wnd.IsModal():
            self.wnd.EndModal(wx.ID_CANCEL)
        elif self.wnd:
            self.wnd.Destroy()