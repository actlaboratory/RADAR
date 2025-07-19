import json
import wx
import globalVars
import views.ViewCreator
from logging import getLogger
from views.baseDialog import *
from recorder import schedule_manager, RecordingSchedule
import datetime
import simpleDialog

class ScheduledProgram(BaseDialog):
    def __init__(self):
        super().__init__("ShowScheduleListBase")
        self.config = globalVars.app.config
        self.log = getLogger("scheduled_program_list")

    def Initialize(self):
        self.log.debug("created")
        super().Initialize(self.app.hMainView.hFrame, _("予約録音一覧"))
        self.InstallControls()
        self.load_schedules()
        return True

    def InstallControls(self):
        """いろんなウィジェットを設置する"""
        self.creator = views.ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.VERTICAL, 20, style=wx.EXPAND|wx.ALL, margin=20)
        
        # 予約録音一覧
        self.lst, programlist = self.creator.virtualListCtrl(_("予約録音一覧"))
        self.lst.AppendColumn(_("放送局"))
        self.lst.AppendColumn(_("番組タイトル"))
        self.lst.AppendColumn(_("開始日時"))
        self.lst.AppendColumn(_("終了日時"))
        self.lst.AppendColumn(_("出力ファイル"))
        self.lst.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_item_activated)
        
        # ボタン（ViewCreatorが自動的にサイザーに追加する）
        self.remove_btn = self.creator.button(_("選択した予約を削除(&D)"), self.on_remove_selected)
        self.refresh_btn = self.creator.button(_("更新(&R)"), self.on_refresh)
        self.close_btn = self.creator.closebutton(_("閉じる(&C)"), self.on_close)
        
        self.close_btn.SetDefault()
        return

    def load_schedules(self):
        """予約録音一覧を読み込み"""
        try:
            self.lst.clear()
            schedules = schedule_manager.get_schedules()
            
            for schedule in schedules:
                start_time_str = schedule.start_time.strftime("%Y-%m-%d %H:%M")
                end_time_str = schedule.end_time.strftime("%Y-%m-%d %H:%M")
                
                self.lst.Append((
                    schedule.station_name,
                    schedule.program_title,
                    start_time_str,
                    end_time_str,
                    schedule.output_path
                ))
            
            if len(schedules) == 0:
                self.remove_btn.Disable()
            else:
                self.remove_btn.Enable()
                
        except Exception as e:
            self.log.error(f"Error loading schedules: {e}")
            simpleDialog.errorDialog(f"予約録音一覧の読み込みに失敗しました: {e}")

    def on_item_activated(self, event):
        """リストアイテムがダブルクリックされた時の処理"""
        try:
            index = self.lst.GetFocusedItem()
            if index >= 0:
                schedules = schedule_manager.get_schedules()
                if index < len(schedules):
                    schedule = schedules[index]
                    self.show_schedule_detail(schedule)
        except Exception as e:
            self.log.error(f"Error in on_item_activated: {e}")

    def show_schedule_detail(self, schedule):
        """予約詳細を表示"""
        try:
            detail_text = f"""予約録音詳細:

放送局: {schedule.station_name}
番組タイトル: {schedule.program_title}
開始日時: {schedule.start_time.strftime("%Y-%m-%d %H:%M")}
終了日時: {schedule.end_time.strftime("%Y-%m-%d %H:%M")}
出力ファイル: {schedule.output_path}
ファイル形式: {schedule.filetype}
繰り返し: {schedule.repeat_type}
有効: {'はい' if schedule.enabled else 'いいえ'}"""
            
            simpleDialog.dialog(_("予約録音詳細"), detail_text)
        except Exception as e:
            self.log.error(f"Error showing schedule detail: {e}")

    def on_remove_selected(self, event):
        """選択した予約を削除"""
        try:
            index = self.lst.GetFocusedItem()
            if index < 0:
                simpleDialog.errorDialog("削除する予約を選択してください。")
                return
            
            schedules = schedule_manager.get_schedules()
            if index >= len(schedules):
                simpleDialog.errorDialog("選択された予約が見つかりません。")
                return
            
            schedule = schedules[index]
            
            # 確認ダイアログ
            result = simpleDialog.yesNoDialog(_("確認"),_(f"以下の予約録音を削除しますか？\n\n放送局: {schedule.station_name}\n番組タイトル: {schedule.program_title}\n開始日時: {schedule.start_time.strftime('%Y-%m-%d %H:%M')}"))
            
            if result:
                schedule_manager.remove_schedule(schedule.id)
                self.load_schedules()
                simpleDialog.dialog(_("削除完了"), "予約録音を削除しました。")
                
        except Exception as e:
            self.log.error(f"Error removing schedule: {e}")
            simpleDialog.errorDialog(f"予約録音の削除に失敗しました: {e}")

    def on_refresh(self, event):
        """一覧を更新"""
        try:
            self.load_schedules()
        except Exception as e:
            self.log.error(f"Error refreshing schedules: {e}")

    def on_close(self, event):
        """ダイアログを閉じる"""
        self.Destroy()
