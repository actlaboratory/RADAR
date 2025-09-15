# -*- coding: utf-8 -*-
# 番組検索ダイアログモジュール

import globalVars
import wx
import datetime
from logging import getLogger
import constants
from views.baseDialog import BaseDialog
import views.ViewCreator
from views.programCacheManager import ProgramCacheManager
from views.programSearchEngine import ProgramSearchEngine
from views.programDataCollector import ProgramDataCollector

class ProgramSearchDialog(BaseDialog):
    """番組検索ダイアログ"""
    
    def __init__(self, radio_manager=None, parent=None):
        super().__init__("ProgramSearchDialog")
        self.radio_manager = radio_manager
        self.parent = parent
        self.log = getLogger(f"{constants.LOG_PREFIX}.ProgramSearchDialog")
        
        # 検索エンジンの初期化
        self.cache_manager = ProgramCacheManager()
        self.search_engine = ProgramSearchEngine(self.cache_manager)
        self.data_collector = ProgramDataCollector(self.cache_manager)
        if radio_manager:
            self.data_collector.set_radio_manager(radio_manager)
        
        # 検索結果
        self.search_results = []
        
    def Initialize(self):
        """ダイアログの初期化"""
        self.log.debug("Initializing ProgramSearchDialog")
        super().Initialize(globalVars.app.hMainView.hFrame, _("番組検索"))
        self.InstallControls()
        return True
    
    def InstallControls(self):
        """コントロールの設置"""
        self.creator = views.ViewCreator.ViewCreator(
            self.viewMode, self.panel, self.sizer, 
            wx.VERTICAL, 20, style=wx.EXPAND|wx.ALL, margin=20
        )
        
        # 検索条件入力エリア
        self.create_search_inputs()
        
        # 検索結果表示エリア
        self.create_results_display()
        
        # ボタンエリア
        self.create_buttons()
        
        # 初期データ収集と日付オプション設定
        self.collect_initial_data()
        self.setup_date_options()
    
    def create_search_inputs(self):
        """検索条件入力エリアを作成"""
        # 番組タイトル検索
        self.title_input, title_label = self.creator.inputbox(_("番組タイトル"), style=wx.TE_PROCESS_ENTER)
        self.title_input.Bind(wx.EVT_TEXT_ENTER, self.onSearch)
        
        # 出演者検索
        self.performer_input, performer_label = self.creator.inputbox(_("出演者"), style=wx.TE_PROCESS_ENTER)
        self.performer_input.Bind(wx.EVT_TEXT_ENTER, self.onSearch)
        
        # 放送局選択
        self.station_combo, station_label = self.creator.combobox(_("放送局"), [])
        self.station_combo.Bind(wx.EVT_COMBOBOX, self.onStationChanged)
        
        # 日付選択（コンボボックス）
        self.date_combo, date_label = self.creator.combobox(_("日付"), [])
        self.date_combo.Bind(wx.EVT_COMBOBOX, self.onDateChanged)
        
        # 時間範囲選択（スピンコントロール）
        time_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        # 開始時間
        start_time_sizer = wx.BoxSizer(wx.HORIZONTAL)
        start_label = wx.StaticText(self.panel, wx.ID_ANY, _("開始時間"))
        self.start_hour_spin = wx.SpinCtrl(self.panel, wx.ID_ANY, value="0", min=0, max=23, size=(60, -1))
        self.start_minute_spin = wx.SpinCtrl(self.panel, wx.ID_ANY, value="0", min=0, max=59, size=(60, -1))
        start_time_sizer.Add(start_label, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)
        start_time_sizer.Add(self.start_hour_spin, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)
        start_time_sizer.Add(wx.StaticText(self.panel, wx.ID_ANY, ":"), 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)
        start_time_sizer.Add(self.start_minute_spin, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)
        
        # 終了時間
        end_time_sizer = wx.BoxSizer(wx.HORIZONTAL)
        end_label = wx.StaticText(self.panel, wx.ID_ANY, _("終了時間"))
        self.end_hour_spin = wx.SpinCtrl(self.panel, wx.ID_ANY, value="23", min=0, max=23, size=(60, -1))
        self.end_minute_spin = wx.SpinCtrl(self.panel, wx.ID_ANY, value="59", min=0, max=59, size=(60, -1))
        end_time_sizer.Add(end_label, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)
        end_time_sizer.Add(self.end_hour_spin, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)
        end_time_sizer.Add(wx.StaticText(self.panel, wx.ID_ANY, ":"), 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)
        end_time_sizer.Add(self.end_minute_spin, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)
        
        time_sizer.Add(start_time_sizer, 0, wx.ALL, 5)
        time_sizer.Add(end_time_sizer, 0, wx.ALL, 5)
        
        self.sizer.Add(time_sizer, 0, wx.EXPAND|wx.ALL, 5)
        
        # 検索ボタン
        self.search_btn = wx.Button(self.panel, wx.ID_ANY, _("検索"))
        self.search_btn.Bind(wx.EVT_BUTTON, self.onSearch)
        self.search_btn.SetDefault()
        
        # クリアボタン
        self.clear_btn = wx.Button(self.panel, wx.ID_ANY, _("クリア"))
        self.clear_btn.Bind(wx.EVT_BUTTON, self.onClear)
        
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(self.search_btn, 0, wx.ALL, 5)
        button_sizer.Add(self.clear_btn, 0, wx.ALL, 5)
        self.sizer.Add(button_sizer, 0, wx.ALIGN_CENTER)
    
    def create_results_display(self):
        """検索結果表示エリアを作成"""
        # 結果リスト
        self.result_list, result_label = self.creator.virtualListCtrl(_("検索結果"))
        self.result_list.AppendColumn(_("放送局"))
        self.result_list.AppendColumn(_("番組タイトル"))
        self.result_list.AppendColumn(_("出演者"))
        self.result_list.AppendColumn(_("開始時間"))
        self.result_list.AppendColumn(_("終了時間"))
        self.result_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.onItemActivated)
        
        # 結果数表示
        self.result_count_label = wx.StaticText(self.panel, wx.ID_ANY, _("結果: 0件"))
        self.sizer.Add(self.result_count_label, 0, wx.ALL, 5)
    
    def create_buttons(self):
        """ボタンエリアを作成"""
        # 閉じるボタン
        self.close_btn = wx.Button(self.panel, wx.ID_CANCEL, _("閉じる"))
        self.close_btn.Bind(wx.EVT_BUTTON, self.onClose)
        
        # データ更新ボタン
        self.refresh_btn = wx.Button(self.panel, wx.ID_ANY, _("データ更新"))
        self.refresh_btn.Bind(wx.EVT_BUTTON, self.onRefresh)
        
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(self.refresh_btn, 0, wx.ALL, 5)
        button_sizer.Add(self.close_btn, 0, wx.ALL, 5)
        self.sizer.Add(button_sizer, 0, wx.ALIGN_RIGHT)
    
    def setup_date_options(self):
        """日付選択オプションを設定（データベースの実際の日付範囲を使用）"""
        try:
            # データベースから利用可能な日付範囲を取得
            if hasattr(self, 'cache_manager') and self.cache_manager:
                date_range = self.cache_manager.get_available_date_range()
                if date_range and date_range['days_available'] > 0:
                    self.log.info(f"Available date range: {date_range['start_date']} to {date_range['end_date']}")
                    # データベースの日付範囲を使用
                    start_date = datetime.datetime.strptime(date_range['start_date'], '%Y%m%d')
                    end_date = datetime.datetime.strptime(date_range['end_date'], '%Y%m%d')
                else:
                    # データベースにデータがない場合は今日から1週間分
                    start_date = datetime.datetime.now()
                    end_date = start_date + datetime.timedelta(days=6)
                    self.log.info("No data in database, using today + 6 days")
            else:
                # フォールバック: 今日から1週間分
                start_date = datetime.datetime.now()
                end_date = start_date + datetime.timedelta(days=6)
                self.log.info("Cache manager not available, using today + 6 days")
            
            # 日付オプションを作成
            date_options = []
            current_date = start_date
            while current_date <= end_date:
                # エンコーディングエラーを避けるため、英語形式で表示
                try:
                    date_str = current_date.strftime('%Y-%m-%d')
                except (ValueError, OSError):
                    # ロケールエラーの場合は代替形式を使用
                    date_str = f"{current_date.year:04d}-{current_date.month:02d}-{current_date.day:02d}"
                
                date_value = current_date.strftime('%Y%m%d')
                date_options.append(f"{date_str} ({date_value})")
                current_date += datetime.timedelta(days=1)
            
            self.date_combo.SetItems(date_options)
            self.date_combo.SetSelection(0)  # 最初の日付を選択
            
            self.log.info(f"Date options set: {len(date_options)} dates from {date_options[0]} to {date_options[-1]}")
            
            # 各日付オプションの詳細をログ出力
            for i, option in enumerate(date_options):
                self.log.debug(f"Date option {i}: '{option}'")
            
        except Exception as e:
            self.log.error(f"Failed to setup date options: {e}")
            # フォールバック: シンプルな日付形式
            try:
                date_options = []
                start_date = datetime.datetime.now()
                for i in range(7):
                    target_date = start_date + datetime.timedelta(days=i)
                    date_value = target_date.strftime('%Y%m%d')
                    date_options.append(f"{date_value}")
                self.date_combo.SetItems(date_options)
                self.date_combo.SetSelection(0)
                self.log.info(f"Fallback date options set: {date_options}")
            except Exception as e2:
                self.log.error(f"Fallback date setup also failed: {e2}")
    
    def collect_initial_data(self):
        """初期データの収集"""
        try:
            # 放送局リストを更新
            self.update_station_list()
            
            # データベースの状態を確認
            if hasattr(self, 'cache_manager') and self.cache_manager:
                summary = self.cache_manager.get_weekly_data_summary()
                if summary:
                    self.log.info(f"Database status: {summary['total_programs']} programs across {summary['total_stations']} stations")
                    self.log.info(f"Date range: {summary['date_range']}")
                    for date_str, count in summary['weekly_summary'].items():
                        self.log.debug(f"Date {date_str}: {count} programs")
                    
                    # 1週間分のデータが不完全な場合は強制更新
                    if not self.cache_manager.is_weekly_cache_complete():
                        self.log.info("Weekly cache is incomplete, forcing weekly data update")
                        if hasattr(self.parent, 'program_cache_controller'):
                            success = self.parent.program_cache_controller.ensure_weekly_data()
                            if success:
                                self.log.info("Weekly data update completed successfully")
                            else:
                                self.log.warning("Weekly data update failed")
                else:
                    self.log.warning("No database summary available")
            
            # 今日のデータを収集（フォールバック）
            self.data_collector.collect_all_stations_data()
            
        except Exception as e:
            self.log.error(f"Failed to collect initial data: {e}")
    
    def _debug_date_in_database(self, search_date):
        """データベースの日付形式をデバッグ"""
        try:
            cursor = self.cache_manager.conn.cursor()
            
            # 指定された日付のデータを確認
            cursor.execute("SELECT DISTINCT date FROM programs WHERE date = ? LIMIT 5", (search_date,))
            exact_matches = cursor.fetchall()
            
            # 類似する日付を確認
            cursor.execute("SELECT DISTINCT date FROM programs ORDER BY date LIMIT 10")
            all_dates = cursor.fetchall()
            
            # 指定された日付の番組数を確認
            cursor.execute("SELECT COUNT(*) FROM programs WHERE date = ?", (search_date,))
            count = cursor.fetchone()[0]
            
            self.log.info(f"Debug - Search date: '{search_date}'")
            self.log.info(f"Debug - Exact matches: {[row[0] for row in exact_matches]}")
            self.log.info(f"Debug - Sample dates in DB: {[row[0] for row in all_dates]}")
            self.log.info(f"Debug - Programs count for search date: {count}")
            
        except Exception as e:
            self.log.error(f"Failed to debug date in database: {e}")
    
    def update_station_list(self):
        """放送局リストを更新"""
        try:
            if self.radio_manager:
                # RadioManagerから放送局リストを取得
                stations = [(sid, name) for sid, name in self.radio_manager.stid.items()]
            else:
                # キャッシュから放送局リストを取得
                stations = self.data_collector.get_station_list()
            
            station_names = [name for _, name in stations]
            self.station_combo.SetItems(station_names)
            
            if station_names:
                self.station_combo.SetSelection(0)
                
        except Exception as e:
            self.log.error(f"Failed to update station list: {e}")
    
    def onSearch(self, event):
        """検索実行"""
        self._safe_busy_cursor(self._perform_search)
    
    def _perform_search(self):
        """検索の実際の処理"""
        # 検索条件を取得
        search_criteria = self.get_search_criteria()
        
        # 検索条件が空の場合は警告
        if not search_criteria:
            wx.MessageBox(_("検索条件を入力してください。"), _("警告"), wx.OK|wx.ICON_WARNING)
            return
        
        # デバッグ情報をログ出力
        self.log.info(f"Search criteria: {search_criteria}")
        
        # データベースの日付形式を確認
        if hasattr(self, 'cache_manager') and self.cache_manager and 'date' in search_criteria:
            self._debug_date_in_database(search_criteria['date'])
        
        # 検索実行
        use_time_range = search_criteria.pop('use_time_range_search', False)
        self.search_results = self.search_engine.search_combined(
            use_time_range_search=use_time_range,
            **search_criteria
        )
        
        # 検索結果のデバッグ情報
        self.log.info(f"Search completed: {len(self.search_results)} results found")
        if self.search_results:
            self.log.debug(f"First result: {self.search_results[0]}")
        
        # 結果を表示
        self.display_results()
    
    def get_search_criteria(self):
        """検索条件を取得"""
        criteria = {}
        
        # 番組タイトル
        title = self.title_input.GetValue().strip()
        if title:
            criteria['title'] = title
        
        # 出演者
        performer = self.performer_input.GetValue().strip()
        if performer:
            criteria['performer'] = performer
        
        # 放送局
        station_name = self.station_combo.GetValue().strip()
        if station_name:
            criteria['station_name'] = station_name
        
        # 日付（コンボボックスから取得）
        date_selection = self.date_combo.GetSelection()
        if date_selection >= 0:
            date_text = self.date_combo.GetString(date_selection)
            self.log.debug(f"Selected date text: '{date_text}'")
            
            # 括弧内の日付値を抽出 (例: "2024-01-15 (20240115)" → "20240115")
            if '(' in date_text and ')' in date_text:
                date_value = date_text.split('(')[1].split(')')[0]
                criteria['date'] = date_value
                self.log.debug(f"Extracted date value: '{date_value}'")
            else:
                # フォールバック: 括弧がない場合はそのまま使用
                criteria['date'] = date_text
                self.log.debug(f"Using date text as is: '{date_text}'")
        
        # 時間範囲（スピンコントロールから取得）
        start_hour = self.start_hour_spin.GetValue()
        start_minute = self.start_minute_spin.GetValue()
        end_hour = self.end_hour_spin.GetValue()
        end_minute = self.end_minute_spin.GetValue()
        
        # 時間範囲の設定
        # 開始時間が設定されている場合（0:00以外）
        if start_hour > 0 or start_minute > 0:
            criteria['start_time'] = f"{start_hour:02d}:{start_minute:02d}:00"
        
        # 終了時間が設定されている場合（23:59以外）
        if not (end_hour == 23 and end_minute == 59):
            criteria['end_time'] = f"{end_hour:02d}:{end_minute:02d}:00"
        
        # 時間範囲の妥当性チェック
        if 'start_time' in criteria and 'end_time' in criteria:
            start_time_str = criteria['start_time']
            end_time_str = criteria['end_time']
            if start_time_str >= end_time_str:
                # 開始時間が終了時間より遅い場合は警告
                wx.MessageBox(_("開始時間は終了時間より早く設定してください。"), _("警告"), wx.OK|wx.ICON_WARNING)
                return {}
        
        # 時間範囲検索のフラグを設定
        criteria['use_time_range_search'] = True
        
        return criteria
    
    def display_results(self):
        """検索結果を表示"""
        self.result_list.clear()
        
        if not self.search_results:
            # 結果がない場合のメッセージ
            self.result_list.Append((_("検索結果がありません"), "", "", "", ""))
            self.result_count_label.SetLabel(_("結果: 0件"))
            return
        
        for program in self.search_results:
            # 時間の表示を整形
            start_time = program.get('start_time', '')
            end_time = program.get('end_time', '')
            
            # HH:MM:SS形式からHH:MM形式に変換
            if start_time and len(start_time) >= 5:
                start_time = start_time[:5]
            if end_time and len(end_time) >= 5:
                end_time = end_time[:5]
            
            # 日付情報を追加
            date = program.get('date', '')
            self.log.debug(f"Program date: '{date}' (type: {type(date)})")
            
            if date and len(date) == 8:
                # YYYYMMDD形式をYYYY/MM/DD形式に変換
                formatted_date = f"{date[:4]}/{date[4:6]}/{date[6:8]}"
            else:
                formatted_date = date
            
            # 放送局名に日付を追加
            station_name = program.get('station_name', '')
            if formatted_date:
                station_name = f"[{formatted_date}] {station_name}"
            
            self.result_list.Append((
                station_name,
                program.get('title', ''),
                program.get('performer', ''),
                start_time,
                end_time
            ))
        
        # 結果数を更新
        count = len(self.search_results)
        self.result_count_label.SetLabel(_(f"結果: {count}件"))
        
        if count > 0:
            self.result_list.Focus(0)
            # 結果数をログ出力
            self.log.info(f"Displayed {count} search results")
    
    def onItemActivated(self, event):
        """リストアイテムがダブルクリックされた時の処理"""
        try:
            index = event.GetIndex()
            if 0 <= index < len(self.search_results) and len(self.search_results) > 0:
                program = self.search_results[index]
                self.show_program_detail(program)
        except Exception as e:
            self.log.error(f"Failed to show program detail: {e}")
    
    def show_program_detail(self, program):
        """番組詳細を表示"""
        try:
            # 既存の番組詳細ダイアログを使用
            from views import programdetail
            pd = programdetail.dialog()
            
            # 番組情報を設定
            pd.show_title([program.get('title', '')], 0)
            pd.show_pfm([program.get('performer', '')], 0)
            pd.show_starttime([program.get('start_time', '')], 0)
            pd.show_endtime([program.get('end_time', '')], 0)
            pd.show_dsc([program.get('description', '')], 0)
            
            pd.Initialize()
            pd.Show()
            
        except Exception as e:
            self.log.error(f"Failed to show program detail: {e}")
            wx.MessageBox(_("番組詳細の表示に失敗しました。"), _("エラー"), wx.OK|wx.ICON_ERROR)
    
    def onClear(self, event):
        """検索条件をクリア"""
        self.title_input.SetValue("")
        self.performer_input.SetValue("")
        self.station_combo.SetValue("")
        self.date_combo.SetSelection(0)  # 今日を選択
        
        # スピンコントロールをリセット
        self.start_hour_spin.SetValue(0)
        self.start_minute_spin.SetValue(0)
        self.end_hour_spin.SetValue(23)
        self.end_minute_spin.SetValue(59)
        
        self.result_list.clear()
        self.result_count_label.SetLabel(_("結果: 0件"))
    
    def onStationChanged(self, event):
        """放送局が変更された時の処理"""
        # 必要に応じて実装
        pass
    
    def onDateChanged(self, event):
        """日付が変更された時の処理"""
        # 必要に応じて実装
        pass
    
    def onRefresh(self, event):
        """データ更新"""
        self._safe_busy_cursor(self._perform_data_refresh)
    
    def _safe_busy_cursor(self, func):
        """安全なBusyCursorの実行"""
        busy_cursor_active = False
        try:
            wx.BusyCursor()
            busy_cursor_active = True
            func()
        except Exception as e:
            self.log.error(f"Operation failed: {e}")
            wx.MessageBox(_("操作中にエラーが発生しました。"), _("エラー"), wx.OK|wx.ICON_ERROR)
        finally:
            if busy_cursor_active:
                try:
                    wx.EndBusyCursor()
                except wx._core.wxAssertionError:
                    # アサーションエラーが発生した場合は無視
                    self.log.warning("wxEndBusyCursor assertion error ignored")
    
    def _perform_data_refresh(self):
        """データ更新の実際の処理"""
        # キャッシュを無効化して強制更新
        if hasattr(self, 'cache_manager'):
            # キャッシュメタデータをクリア
            cursor = self.cache_manager.conn.cursor()
            cursor.execute("DELETE FROM cache_metadata WHERE key = 'last_update'")
            self.cache_manager.conn.commit()
            self.log.info("Cache metadata cleared for force refresh")
        
        # 1週間分のデータを強制更新
        success = False
        if hasattr(self.parent, 'program_cache_controller'):
            self.log.info("Starting forced weekly data update")
            success = self.parent.program_cache_controller.force_weekly_update()
        
        # フォールバック: 今日のデータのみ収集
        if not success:
            self.log.warning("Weekly update failed, falling back to today's data only")
            success = self.data_collector.collect_all_stations_data(force_refresh=True)
        
        if success:
            wx.MessageBox(_("データの更新が完了しました。"), _("完了"), wx.OK|wx.ICON_INFORMATION)
            self.update_station_list()
        else:
            wx.MessageBox(_("データの更新に失敗しました。"), _("エラー"), wx.OK|wx.ICON_ERROR)
    
    def onClose(self, event):
        """ダイアログを閉じる"""
        self.Destroy()
    
    def cleanup(self):
        """リソースのクリーンアップ"""
        if self.data_collector:
            self.data_collector.cleanup()
        if self.cache_manager:
            self.cache_manager.close()
