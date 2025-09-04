# -*- coding: utf-8 -*-
# recording manager dialog
# 録音管理ダイアログ

import wx
import datetime
from logging import getLogger
from views.baseDialog import *
import views.ViewCreator
from recorder import recorder_manager
from plyer import notification

class RecordingManagerDialog(BaseDialog):
    """録音管理ダイアログ"""
    
    def __init__(self):
        super().__init__("RecordingManagerDialog")
        self.log = getLogger("recording_manager")
        self.recorder_manager = recorder_manager
        self.active_recorders = []
        self.timer = None

    def Initialize(self):
        """ダイアログを初期化"""
        self.log.debug("created")
        super().Initialize(globalVars.app.hMainView.hFrame, _("録音管理"))
        self.InstallControls()
        self.load_recordings()
        
        # 自動更新タイマーを開始（5秒ごと）
        self.timer = wx.Timer()
        self.timer.Bind(wx.EVT_TIMER, self.onRefresh)
        self.timer.Start(5000)
        
        return True

    def InstallControls(self):
        """コントロールを配置"""
        self.creator = views.ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.VERTICAL, 20, style=wx.EXPAND|wx.ALL, margin=20)
        
        # 録音一覧リスト
        self.lst, programlist = self.creator.virtualListCtrl(_("現在の録音一覧"))
        self.lst.AppendColumn(_("放送局"))
        self.lst.AppendColumn(_("番組名"))
        self.lst.AppendColumn(_("開始時刻"))
        self.lst.AppendColumn(_("ファイル名"))
        self.lst.AppendColumn(_("状態"))
        
        # ボタン
        self.refresh_btn = self.creator.button(_("更新(&R)"), self.onRefresh)
        self.stop_btn = self.creator.button(_("選択した録音を停止(&S)"), self.onStop)
        self.stop_all_btn = self.creator.button(_("全て停止(&A)"), self.onStopAll)
        self.close_btn = self.creator.closebutton(_("閉じる(&X)"), self.onClose)
        self.close_btn.SetDefault()

    def load_recordings(self):
        """録音一覧を読み込み"""
        try:
            self.active_recorders = self.recorder_manager.get_active_recorders()
            self.update_list()
        except Exception as e:
            self.log.error(f"Failed to load recordings: {e}")

    def update_list(self):
        """リストを更新"""
        self.lst.clear()
        for recorder, info in self.active_recorders:
            # 情報を解析
            parts = info.split(' ', 1)  # 放送局名と番組名を分離
            station_name = parts[0] if parts else "不明"
            program_title = parts[1] if len(parts) > 1 else "不明"
            
            # ファイル名を取得
            file_name = f"{recorder.output_path}.{recorder.filetype}"
            file_name = file_name.split('\\')[-1]  # パスからファイル名のみ抽出
            
            # 開始時刻を取得（現在時刻を仮設定）
            start_time = datetime.datetime.now().strftime("%H:%M:%S")
            
            # 状態
            status = _("録音中")
            
            self.lst.Append((
                station_name,
                program_title,
                start_time,
                file_name,
                status
            ))

    def onRefresh(self, event):
        """リストを更新"""
        self.load_recordings()

    def onStop(self, event):
        """選択された録音を停止"""
        try:
            selected = self.lst.GetFocusedItem()
            if selected < 0:
                wx.MessageBox(_("録音を選択してください。"), _("エラー"), wx.OK | wx.ICON_ERROR)
                return
            
            if selected >= len(self.active_recorders):
                wx.MessageBox(_("選択された録音が見つかりません。"), _("エラー"), wx.OK | wx.ICON_ERROR)
                return
            
            recorder, info = self.active_recorders[selected]
            
            # 確認ダイアログ
            result = wx.MessageBox(
                f"'{info}' の録音を停止しますか？",
                _("確認"),
                wx.YES_NO | wx.ICON_QUESTION
            )
            
            if result == wx.YES:
                self.recorder_manager.stop_recorder(recorder)
                
                # 通知
                notification.notify(
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
            
            # 確認ダイアログ
            result = wx.MessageBox(
                f"全ての録音（{len(self.active_recorders)}件）を停止しますか？",
                _("確認"),
                wx.YES_NO | wx.ICON_QUESTION
            )
            
            if result == wx.YES:
                self.recorder_manager.stop_all()
                
                # 通知
                notification.notify(
                    title='録音停止',
                    message='全ての録音を停止しました。',
                    app_name='rpb',
                    timeout=10
                )
                
                self.load_recordings()
                
        except Exception as e:
            self.log.error(f"Error stopping all recordings: {e}")
            wx.MessageBox(f"録音の停止に失敗しました: {e}", _("エラー"), wx.OK | wx.ICON_ERROR)

    def onClose(self, event):
        """ダイアログを閉じる"""
        if self.timer:
            self.timer.Stop()
        event.Skip()