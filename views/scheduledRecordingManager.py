import wx
import datetime
from logging import getLogger
from views.baseDialog import *
import views.ViewCreator
from recorder import schedule_manager, RECORDING_STATUS_SCHEDULED, RECORDING_STATUS_RECORDING, RECORDING_STATUS_COMPLETED, RECORDING_STATUS_CANCELLED, RECORDING_STATUS_FAILED

class ScheduledRecordingManager(BaseDialog):
    """スケジュール録音一覧ダイアログ"""
    def __init__(self):
        super().__init__("ScheduledRecordingManager")
        self.log = getLogger("scheduled_recording_manager")
        self.schedules = []

    def Initialize(self):
        self.log.debug("created")
        super().Initialize(globalVars.app.hMainView.hFrame, _("スケジュール録音一覧"))
        self.InstallControls()
        self.load_schedules()
        return True

    def InstallControls(self):
        """コントロールを配置"""
        self.creator = views.ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.VERTICAL, 20, style=wx.EXPAND|wx.ALL, margin=20)
        
        # 予約一覧リスト
        self.lst, programlist = self.creator.virtualListCtrl(_("スケジュール録音一覧"), size=(800,400))
        self.lst.AppendColumn(_("番組タイトル"),0,280)
        self.lst.AppendColumn(_("放送局"),0,200)
        self.lst.AppendColumn(_("開始時間"),0,100)
        self.lst.AppendColumn(_("終了時間"),0,100)
        self.lst.AppendColumn(_("ステータス"),0,100)
        self.lst.AppendColumn(_("出力パス"),0,500)
        
        # ボタン
        horizontalCreator = views.ViewCreator.ViewCreator(self.viewMode, self.creator.GetPanel(), self.creator.GetSizer(), wx.HORIZONTAL, 20, style=wx.EXPAND|wx.ALL, margin=20)
        self.refresh_btn = horizontalCreator.button(_("更新(&R)"), self.onRefresh)
        horizontalCreator.AddSpace(-1)
        self.cancel_btn = horizontalCreator.button(_("キャンセル(&C)"), self.onCancel)
        horizontalCreator.AddSpace(-1)
        self.remove_btn = horizontalCreator.button(_("削除(&D)"), self.onRemove)

        horizontalCreator = views.ViewCreator.ViewCreator(self.viewMode, self.creator.GetPanel(), self.creator.GetSizer(), wx.HORIZONTAL, 20, style=wx.EXPAND|wx.ALL, margin=20)
        self.clear_all_btn = horizontalCreator.button(_("すべて削除(&A)"), self.onClearAll)
        horizontalCreator.AddSpace(-1)
        self.close_btn = horizontalCreator.closebutton(_("閉じる(&X)"), self.onClose, sizerFlag=wx.ALIGN_RIGHT)
        self.close_btn.SetDefault()

    def load_schedules(self):
        """予約一覧を読み込み"""
        try:
            all_schedules = schedule_manager.get_schedules()
            # 完了済みのスケジュールを除外
            self.schedules = [s for s in all_schedules if s.status != RECORDING_STATUS_COMPLETED]
            self.update_list()
        except Exception as e:
            self.log.error(f"Failed to load schedules: {e}")

    def update_list(self):
        """リストを更新"""
        self.lst.clear()
        for schedule in self.schedules:
            start_time_str = schedule.start_time.strftime("%Y-%m-%d %H:%M")
            end_time_str = schedule.end_time.strftime("%Y-%m-%d %H:%M")
            status_display = schedule.get_status_display_name()
            
            self.lst.Append((
                schedule.program_title,
                schedule.station_name,
                start_time_str,
                end_time_str,
                status_display,
                schedule.output_path
            ))

    def onRefresh(self, event):
        """リストを更新"""
        self.load_schedules()

    def onCancel(self, event):
        """選択された予約をキャンセル"""
        try:
            selected = self.lst.GetFocusedItem()
            if selected < 0:
                wx.MessageBox(_("予約を選択してください。"), _("エラー"), wx.OK | wx.ICON_ERROR)
                return
            
            schedule = self.schedules[selected]
            if schedule.status in [RECORDING_STATUS_COMPLETED, RECORDING_STATUS_CANCELLED, RECORDING_STATUS_FAILED]:
                wx.MessageBox(_("この予約は既に完了またはキャンセル済みです。"), _("エラー"), wx.OK | wx.ICON_ERROR)
                return
            
            # 確認ダイアログ
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

    def onRemove(self, event):
        """選択された予約を削除"""
        try:
            selected = self.lst.GetFocusedItem()
            if selected < 0:
                wx.MessageBox(_("予約を選択してください。"), _("エラー"), wx.OK | wx.ICON_ERROR)
                return
            
            schedule = self.schedules[selected]
            
            # 確認ダイアログ
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

    def onClearAll(self, event):
        """すべての予約を削除"""
        try:
            if not self.schedules:
                wx.MessageBox(_("削除する予約がありません。"), _("情報"), wx.OK | wx.ICON_INFORMATION)
                return
            
            # 確認ダイアログ
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
        event.Skip()
