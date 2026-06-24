# -*- coding: utf-8 -*-
# 番組検索ダイアログモジュール

import globalVars
import wx
import datetime
import os
import re
import time
import threading
from logging import getLogger
import constants
import simpleDialog
from views.baseDialog import BaseDialog
import views.ViewCreator
from views.programCacheManager import ProgramCacheManager
from views.programSearchEngine import ProgramSearchEngine
from views.programDataCollector import ProgramDataCollector
from views.databaseUpdateProgress import DatabaseUpdateProgress
from views import programmanager
from searchHistoryManager import SearchHistoryManager
from recorder import schedule_manager, RecordingSchedule, recorder_manager, create_recording_dir, get_file_type_from_config
from notification_util import notify as notification_notify
from tcutil import CalendarUtil

class ProgramSearchDialog(BaseDialog):
    """番組検索ダイアログ"""

    DEFAULT_START_HOUR = 5
    DEFAULT_START_MINUTE = 0
    DEFAULT_END_HOUR = 28
    DEFAULT_END_MINUTE = 59
    
    def __init__(self, radio_manager=None):
        super().__init__("ProgramSearchDialog")

        try:
            self.radio_manager = radio_manager or getattr(globalVars.app.hMainView, 'radio_manager', None)
        except (AttributeError, NameError):
            self.radio_manager = radio_manager
        
        self.log = getLogger(f"{constants.LOG_PREFIX}.ProgramSearchDialog")
        
        # 起動時に初期化済みのキャッシュ/検索エンジンを優先的に利用
        self.cache_manager = None
        self.search_engine = None
        self.data_collector = None
        try:
            controller = getattr(globalVars.app.hMainView, 'program_cache_controller', None)
            if controller and getattr(controller, 'cache_manager', None) and getattr(controller, 'search_engine', None):
                self.cache_manager = controller.cache_manager
                self.search_engine = controller.search_engine
                self.log.debug("Using shared cache/search engine from ProgramCacheController")
        except Exception:
            pass

        # フォールバック（万一起動時の初期化が未実行/失敗している場合のみ）
        if self.cache_manager is None:
            self.cache_manager = ProgramCacheManager()
        if self.search_engine is None:
            self.search_engine = ProgramSearchEngine(self.cache_manager)

        # データ収集器は必要時にのみ生成（更新フォールバック用）
        # radio_manager がある場合は設定する
        # 実際の生成は _perform_data_refresh のフォールバックで行う
        
        # 検索結果
        self.search_results = []
        self._base_date_options = []
        self._refresh_worker = None
        self._refresh_state = None
        self._refresh_poller = None

        # 検索履歴管理
        self.history_manager = SearchHistoryManager()
        self.history_enabled = False  # デフォルトは無効
        
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
        
        # 初期表示高速化のため、重いデータ収集は実行しない
        # 放送局と日付オプションのセットのみ行う
        self.update_station_list()
        self.setup_date_options()
        
        # 履歴の初期状態を設定
        self.setup_history_initial_state()
    
    def create_search_inputs(self):
        """検索条件入力エリアを作成"""

        creator = views.ViewCreator.ViewCreator(self.viewMode, self.panel, self.creator.GetSizer(), views.ViewCreator.GridBagSizer,style=wx.ALL|wx.EXPAND,proportion=1,margin=20)

        # 番組タイトル検索（コンボボックス）
        self.title_combo, title_label = creator.combobox(_("番組タイトル"), [], event=self.onTitleComboChanged, style=wx.CB_DROPDOWN, sizerFlag=wx.ALL|wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.title_combo.Bind(wx.EVT_TEXT_ENTER, self.onSearch)

        # 出演者検索（コンボボックス）
        self.performer_combo, performer_label = creator.combobox(_("出演者"), [], event=self.onPerformerComboChanged, style=wx.CB_DROPDOWN, sizerFlag=wx.ALL|wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.performer_combo.Bind(wx.EVT_TEXT_ENTER, self.onSearch)
        
        # 放送局選択
        self.station_combo, station_label = creator.combobox(_("放送局"), [])
        self.station_combo.Bind(wx.EVT_COMBOBOX, self.onStationChanged)
        
        # 開始日時（コンボボックス・スピンコントロール）
        creator.staticText(_("開始日時"))
        date_creator = views.ViewCreator.ViewCreator(
            self.viewMode, self.panel, creator.GetSizer(),
            wx.HORIZONTAL, 20, style=wx.EXPAND|wx.ALL, margin=20
        )

        self.date_combo, date_label = date_creator.combobox(
            _("日付"), [], textLayout=None, style=wx.CB_READONLY
        )
        self.date_combo.Bind(wx.EVT_COMBOBOX, self.onDateChanged)

        self.start_hour_spin, _label = date_creator.spinCtrl(_("開始時間（時）"), min=5, max=28, defaultValue=5, style=wx.SP_ARROW_KEYS, x=-1, proportion=0, margin=5,textLayout=None)
        date_creator.staticText(":")
        self.start_minute_spin, _label = date_creator.spinCtrl(_("開始時間（分）"), min=0, max=59, defaultValue=0, style=wx.SP_ARROW_KEYS, x=-1, proportion=0, margin=5,textLayout=None)

        # 終了時間
        creator.staticText(_("終了時間"))
        date_creator = views.ViewCreator.ViewCreator(            self.viewMode, self.panel, creator.GetSizer(),wx.HORIZONTAL, 20, style=wx.EXPAND|wx.ALL, margin=20)
        self.end_hour_spin, _label = date_creator.spinCtrl(_("終了時間（時）"), min=0, max=28, defaultValue=28, style=wx.SP_ARROW_KEYS, x=-1, proportion=0, margin=5,textLayout=None)
        date_creator.staticText(":")
        self.end_minute_spin, _label = date_creator.spinCtrl(_("終了時間（分）"), min=0, max=59, defaultValue=59, style=wx.SP_ARROW_KEYS, x=-1, proportion=0, margin=5,textLayout=None)

        # 検索・クリアボタン
        button_area_creator = views.ViewCreator.ViewCreator(self.viewMode,self.panel,self.creator.GetSizer(),wx.HORIZONTAL,style=wx.ALIGN_RIGHT)
        self.search_btn = button_area_creator.okbutton(_("検索"), event=self.onSearch)
        self.clear_btn = button_area_creator.button(_("クリア"), event=self.onClear)

    def create_results_display(self):
        """検索結果表示エリアを作成"""
        # 結果リスト
        self.result_list, self.result_count_label = self.creator.virtualListCtrl(_("検索結果"), size=(700,300), sizerFlag=wx.ALL|wx.EXPAND)
        self.result_list.AppendColumn(_("放送局"),0,300)
        self.result_list.AppendColumn(_("番組タイトル"),0,200)
        self.result_list.AppendColumn(_("出演者"),0,100)
        self.result_list.AppendColumn(_("開始"),0,120)
        self.result_list.AppendColumn(_("終了"),0,120)
        self.result_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.onItemActivated)

    def create_buttons(self):
        """ボタンエリアを作成"""
        # 履歴管理
        button_area_creator = views.ViewCreator.ViewCreator(self.viewMode,self.panel,self.creator.GetSizer(),wx.HORIZONTAL, style=wx.EXPAND)
        # 検索履歴を残すチェックボックス
        self.history_checkbox = button_area_creator.checkbox(_("検索履歴を残す"), event=self.onHistoryCheckboxChanged)

        # 見た目の調整
        button_area_creator.AddSpace(-1)

        # 履歴クリアボタン
        self.history_clear_btn = button_area_creator.button(_("履歴クリア"), event=self.onHistoryClear)
        self.history_clear_btn.Enable(False)  # デフォルトは無効

        button_area_creator = views.ViewCreator.ViewCreator(self.viewMode,self.panel,self.creator.GetSizer(),wx.HORIZONTAL, style=wx.EXPAND)
        # 予約録音ボタン
        self.schedule_btn = button_area_creator.button(_("予約録音(&R)"), event=self.onScheduleRecording)
        self.schedule_btn.Enable(False)
        self.timefree_play_btn = button_area_creator.button(_("聴き逃し再生(&F)"), event=self.on_play_timefree)
        self.timefree_rec_btn = button_area_creator.button(_("聴き逃し録音(&T)"), event=self.on_record_timefree)
        self.timefree_play_btn.Enable(False)
        self.timefree_rec_btn.Enable(False)

        # 見た目の調整
        button_area_creator.AddSpace(-1)
        
        # データ更新ボタン
        self.refresh_btn = button_area_creator.button(_("データ更新"), event=self.onRefresh)

        # 見た目の調整
        button_area_creator.AddSpace(-1)

        # 閉じるボタン
        self.close_btn = button_area_creator.cancelbutton(_("閉じる"), event=self.onClose)
        
        # 検索結果リストの選択変更イベントをバインド
        self.result_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onItemSelected)
        self.result_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.onItemDeselected)

    def setup_date_options(self):
        """日付選択を番組表と同じく、ラジオ日付(5時切替)基準の8日分に揃える"""
        try:
            clutl = CalendarUtil()
            date_strings = clutl.getDateValue()
            date_options = []
            for ds in date_strings:
                ymd = clutl.transform_date(ds)
                try:
                    dt = datetime.datetime.strptime(ymd, '%Y%m%d')
                    date_str = dt.strftime('%Y/%m/%d')
                except ValueError:
                    date_str = f"{ds}"
                date_options.append(f"{date_str} ({ymd})")

            self._base_date_options = list(date_options)
            self._apply_date_options()

            self.log.info(
                "Date options aligned with program schedule (radio calendar, %s days)",
                len(date_strings),
            )
            for i, option in enumerate(date_options):
                self.log.debug(f"Date option {i}: '{option}'")

        except Exception as e:
            self.log.error(f"Failed to setup date options: {e}")
            try:
                clutl = CalendarUtil()
                date_strings = clutl.getDateValue()
                date_options = []
                for ds in date_strings:
                    ymd = clutl.transform_date(ds)
                    dt = datetime.datetime.strptime(ymd, '%Y%m%d')
                    date_options.append(f"{dt.strftime('%Y/%m/%d')} ({ymd})")
                self._base_date_options = list(date_options)
                self._apply_date_options()
                self.log.info("Fallback date options set via CalendarUtil")
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
                        try:
                            if hasattr(globalVars.app.hMainView, 'program_cache_controller'):
                                success = globalVars.app.hMainView.program_cache_controller.ensure_weekly_data()
                                if success:
                                    self.log.info("Weekly data update completed successfully")
                                else:
                                    self.log.warning("Weekly data update failed")
                        except (AttributeError, NameError) as e:
                            self.log.warning(f"Failed to access program_cache_controller: {e}")
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
            # 最初に「指定なし」を追加
            station_names.insert(0, _("指定なし"))
            self.station_combo.SetItems(station_names)
            
            if station_names:
                self.station_combo.SetSelection(0)  # 「指定なし」を選択
                
        except Exception as e:
            self.log.error(f"Failed to update station list: {e}")
    
    def onSearch(self, event):
        """検索実行"""
        try:
            self._perform_search()
        except Exception as e:
            self.log.error(f"Operation failed: {e}")
            simpleDialog.errorDialog(_("操作中にエラーが発生しました。"))
    
    def _perform_search(self):
        """検索の実際の処理"""
        # 検索条件を取得
        search_criteria = self.get_search_criteria()
        
        # 検索条件が空の場合は警告
        if not self._has_meaningful_search_criteria(search_criteria):
            simpleDialog.dialog(_("警告"), _("検索条件を入力してください。"))
            return
        
        # デバッグ情報をログ出力
        self.log.info(f"Search criteria: {search_criteria}")
        
        # データベースの日付形式を確認
        if hasattr(self, 'cache_manager') and self.cache_manager and 'date' in search_criteria:
            self._debug_date_in_database(search_criteria['date'])
        
        # 検索実行
        include_past_when_no_date = search_criteria.pop('include_past_when_no_date', False)
        use_time_range = search_criteria.pop('use_time_range_search', False)
        requested_limit = search_criteria.get('limit', 100)
        self.search_results = self.search_engine.search_combined(
            use_time_range_search=use_time_range,
            **search_criteria
        )

        # 日付未指定時は、必要な場合のみ過去番組を含める
        if not search_criteria.get('date') and not include_past_when_no_date:
            self.search_results = self._filter_future_or_live_programs(self.search_results)

        self.search_results.sort(
            key=lambda p: (p.get('date') or '', p.get('start_time') or '')
        )
        if len(self.search_results) > requested_limit:
            self.search_results = self.search_results[:requested_limit]
        
        # 検索結果のデバッグ情報
        self.log.info(f"Search completed: {len(self.search_results)} results found")
        if self.search_results:
            self.log.debug(f"First result: {self.search_results[0]}")
        
        # 結果を表示
        self.display_results()
    
    def _period_scope_labels(self):
        """単一コンボの先頭2件（日付未指定モード）。"""
        return (_("未来・放送中のみ"), _("全期間（過去含む）"))

    def _is_default_time_range(self, start_hour, start_minute, end_hour, end_minute):
        return (
            start_hour == self.DEFAULT_START_HOUR
            and start_minute == self.DEFAULT_START_MINUTE
            and end_hour == self.DEFAULT_END_HOUR
            and end_minute == self.DEFAULT_END_MINUTE
        )

    def _has_meaningful_search_criteria(self, criteria):
        """ユーザーが意図した検索条件があるか（デフォルト時間帯は除外）"""
        if not criteria:
            return False
        if criteria.get('title') or criteria.get('performer') or criteria.get('station_name'):
            return True
        if criteria.get('date'):
            return True
        if criteria.get('start_time') or criteria.get('end_time'):
            return True
        return False

    def get_search_criteria(self):
        """検索条件を取得"""
        criteria = {}
        future_lbl, all_lbl = self._period_scope_labels()

        # 番組タイトル
        title = self.title_combo.GetValue().strip()
        if title:
            criteria['title'] = title
            # 履歴が有効な場合は履歴に追加
            if self.history_enabled:
                self.history_manager.add_title_history(title)
        
        # 出演者
        performer = self.performer_combo.GetValue().strip()
        if performer:
            criteria['performer'] = performer
            # 履歴が有効な場合は履歴に追加
            if self.history_enabled:
                self.history_manager.add_performer_history(performer)
        
        # 放送局
        station_name = self.station_combo.GetValue().strip()
        # 「指定なし」が選択されている場合は検索条件に含めない
        if station_name and station_name != _("指定なし"):
            criteria['station_name'] = station_name
        
        # 日付／期間（単一コンボ）：先頭2件は全日付検索モード、その他は単一日付指定
        date_selection = self.date_combo.GetSelection()
        date_text_scope = ""
        selected_date_value = None
        if date_selection >= 0:
            date_text = self.date_combo.GetString(date_selection)
            date_text_scope = date_text
            self.log.debug(f"Selected date text: '{date_text}'")

            if date_text not in (future_lbl, all_lbl):
                if '(' in date_text and ')' in date_text:
                    selected_date_value = date_text.split('(')[1].split(')')[0].strip()
                else:
                    selected_date_value = date_text.strip()
                if (
                    len(selected_date_value or "") != 8
                    or not str(selected_date_value).isdigit()
                ):
                    simpleDialog.dialog(_("警告"), _("日付リストから日付を選んでください。"))
                    return {}

        if selected_date_value:
            criteria['date'] = selected_date_value

        if 'date' not in criteria:
            criteria['include_past_when_no_date'] = date_text_scope == all_lbl

        # 時間範囲（スピンコントロールから取得）
        start_hour = self.start_hour_spin.GetValue()
        start_minute = self.start_minute_spin.GetValue()
        end_hour = self.end_hour_spin.GetValue()
        end_minute = self.end_minute_spin.GetValue()

        # デフォルト値（5:00〜28:59）のままなら時間条件は付けない
        if not self._is_default_time_range(start_hour, start_minute, end_hour, end_minute):
            # 開始時間が設定されている場合（5:00以降）
            if start_hour >= 5:
                criteria['start_time'] = f"{start_hour:02d}:{start_minute:02d}:00"

            # 終了時間が設定されている場合（28:59以外、ラジオ形式の最大値）
            if not (end_hour == self.DEFAULT_END_HOUR and end_minute == self.DEFAULT_END_MINUTE):
                criteria['end_time'] = f"{end_hour:02d}:{end_minute:02d}:00"

            # 時間範囲の妥当性チェック
            if 'start_time' in criteria and 'end_time' in criteria:
                start_time_str = criteria['start_time']
                end_time_str = criteria['end_time']
                if start_time_str >= end_time_str:
                    simpleDialog.dialog(_("警告"), _("開始時間は終了時間より早く設定してください。"))
                    return {}

            criteria['use_time_range_search'] = True
        
        return criteria

    def _filter_future_or_live_programs(self, programs):
        """日付未指定時に過去番組を除外（放送中/未来のみ残す）"""
        if not programs:
            return programs
        now = datetime.datetime.now()
        out = []
        for program in programs:
            _start_dt, end_dt = self._program_start_end_dt(program)
            if end_dt is None:
                continue
            if end_dt > now:
                out.append(program)
        return out
    
    def display_results(self):
        """検索結果を表示"""
        self.result_list.clear()
        
        if not self.search_results:
            self.result_list.Append((_("検索結果がありません"), "", "", "", ""))
            self.result_count_label.SetLabel(_("検索結果: 0件"))
            self._update_recording_action_buttons(-1)
            try:
                globalVars.app.say(_("結果 0件"), interrupt=True)
            except Exception:
                pass
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
                # YYYYMMDD形式をMM/DD形式に変換
                formatted_date = f"{date[4:6]}/{date[6:8]}"
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
        self.result_count_label.SetLabel(_(f"検索結果: {count}件"))
        
        if count > 0:
            self.result_list.Focus(0)
            try:
                self.result_list.Select(0)
                self._update_recording_action_buttons(0)
                globalVars.app.say(_(f"結果 {count}件"), interrupt=True)
            except Exception:
                pass
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
    
    def onItemSelected(self, event):
        try:
            index = event.GetIndex()
            self._update_recording_action_buttons(index)
        except Exception as e:
            self.log.error(f"Failed to handle item selection: {e}")

    def onItemDeselected(self, event):
        try:
            if self.result_list.GetSelectedItemCount() == 0:
                self._update_recording_action_buttons(-1)
        except Exception as e:
            self.log.error(f"Failed to handle item deselection: {e}")

    def _get_radio_base_date(self):
        now = datetime.datetime.now()
        if now.hour < 5:
            return now.date() - datetime.timedelta(days=1)
        return now.date()

    def _build_past_week_date_options(self):
        sample = ""
        if self._base_date_options:
            sample = self._base_date_options[0]

        def _format_option(date_obj):
            if "(" in sample and ")" in sample:
                return f"{date_obj.strftime('%Y/%m/%d')} ({date_obj.strftime('%Y%m%d')})"
            return date_obj.strftime("%Y%m%d")

        base = self._get_radio_base_date()
        past_entries = []
        for days in range(7, 0, -1):
            d = base - datetime.timedelta(days=days)
            past_entries.append(_format_option(d))
        return past_entries

    def _extract_date_token(self, option_text):
        text = option_text or ""
        if '(' in text and ')' in text:
            return text.split('(')[1].split(')')[0].strip()
        return text.strip()

    def _apply_date_options(self):
        """未来のみ／全期間の2項目＋過去1週＋番組表と同じ日付列を組み立てる。"""
        future_lbl, all_lbl = self._period_scope_labels()
        prefix_items = [future_lbl, all_lbl]

        schedule_items = list(self._base_date_options) if self._base_date_options else []

        preserved_prefix_index = None
        selected_token = None
        current_selection = self.date_combo.GetSelection()
        if self.date_combo.GetCount() > 0 and 0 <= current_selection < self.date_combo.GetCount():
            cur_text = self.date_combo.GetString(current_selection)
            if cur_text == future_lbl:
                preserved_prefix_index = 0
            elif cur_text == all_lbl:
                preserved_prefix_index = 1
            elif cur_text:
                selected_token = self._extract_date_token(cur_text)

        past_entries = self._build_past_week_date_options() if schedule_items else []

        items = prefix_items + past_entries + schedule_items

        self.date_combo.SetItems(items)

        if preserved_prefix_index is not None:
            self.date_combo.SetSelection(preserved_prefix_index)
        elif selected_token:
            for idx, opt in enumerate(items):
                if self._extract_date_token(opt) == selected_token:
                    self.date_combo.SetSelection(idx)
                    break
            else:
                self.date_combo.SetSelection(0)
        else:
            self.date_combo.SetSelection(0)

    def _parse_clock_on_listing_date(self, base_date, time_str):
        if not time_str:
            return None
        parts = str(time_str).split(":")
        if len(parts) < 2:
            return None
        hour = int(parts[0])
        minute = int(parts[1])
        day_offset = hour // 24
        hour = hour % 24
        d = base_date + datetime.timedelta(days=day_offset)
        return datetime.datetime.combine(d, datetime.time(hour, minute))

    def _extended_hour_in_time_str(self, time_str):
        """Radikoの24時超え表記(24:00〜)かどうか。day_offset 済みのため5時切替補正は不要。"""
        if not time_str:
            return False
        parts = str(time_str).split(":")
        if len(parts) < 2:
            return False
        try:
            return int(parts[0]) >= 24
        except ValueError:
            return False

    def _adjust_listing_datetime(self, dt, time_str, *, is_end=False):
        """04:xx 表記向けの5時切替補正。24時超え表記には適用しない。"""
        if dt is None or self._extended_hour_in_time_str(time_str):
            return dt
        if is_end:
            if dt.time() <= datetime.time(5, 0):
                dt += datetime.timedelta(days=1)
        elif dt.time() < datetime.time(4, 59, 59):
            dt += datetime.timedelta(days=1)
        return dt

    def _program_start_end_dt(self, program):
        date_str = program.get("date", "") or ""
        start_time_str = program.get("start_time", "") or ""
        end_time_str = program.get("end_time", "") or ""
        if len(date_str) != 8 or not start_time_str or not end_time_str:
            return None, None
        year = int(date_str[:4])
        month = int(date_str[4:6])
        day = int(date_str[6:8])
        base_date = datetime.date(year, month, day)
        start_dt = self._parse_clock_on_listing_date(base_date, start_time_str)
        end_dt = self._parse_clock_on_listing_date(base_date, end_time_str)
        if not start_dt or not end_dt:
            return None, None
        start_dt = self._adjust_listing_datetime(start_dt, start_time_str, is_end=False)
        end_dt = self._adjust_listing_datetime(end_dt, end_time_str, is_end=True)
        if end_dt <= start_dt:
            end_dt += datetime.timedelta(days=1)
        return start_dt, end_dt

    def _update_recording_action_buttons(self, index):
        self.schedule_btn.Enable(False)
        self.timefree_play_btn.Enable(False)
        self.timefree_rec_btn.Enable(False)
        if index < 0 or index >= len(self.search_results):
            return
        program = self.search_results[index]
        start_dt, end_dt = self._program_start_end_dt(program)
        if start_dt is None:
            return
        now = datetime.datetime.now()
        mv = globalVars.app.hMainView
        rm = getattr(mv, "radio_manager", None)
        live = rm.is_live_playing() if rm else False

        if start_dt >= now:
            self.schedule_btn.Enable(True)
        elif start_dt <= now < end_dt:
            self.timefree_play_btn.Enable(True)
        elif end_dt <= now and not live:
            self.timefree_play_btn.Enable(True)
            self.timefree_rec_btn.Enable(True)

    def on_play_timefree(self, event):
        try:
            index = self.result_list.GetFocusedItem()
            if index < 0:
                index = self.result_list.GetFirstSelected()
            if index < 0 or index >= len(self.search_results):
                simpleDialog.errorDialog(_("番組を選択してください。"))
                return
            program = self.search_results[index]
            start_dt, end_dt = self._program_start_end_dt(program)
            if not start_dt or not end_dt:
                simpleDialog.errorDialog(_("番組の日時を解釈できませんでした。"))
                return
            now = datetime.datetime.now()
            if start_dt > now:
                simpleDialog.errorDialog(_("未来の番組は聴き逃し再生できません。"))
                return
            stid = program.get("station_id")
            if not stid:
                simpleDialog.errorDialog(_("放送局情報がありません。"))
                return
            progs = getattr(globalVars.app.hMainView, "progs", None) or programmanager.ProgramManager()
            mv = globalVars.app.hMainView
            rm = mv.radio_manager
            # 放送中はタイムフリーではなくライブストリームで再生する
            if start_dt <= now < end_dt:
                rm.play(stid, progs)
                return
            title = program.get("title", "")
            station_name = program.get("station_name", "")
            announce = f"聴き逃し再生: {station_name} {title}"
            duration_sec = int(max(1, (end_dt - start_dt).total_seconds()))
            timefree_info = {
                "station_id": stid,
                "station_name": station_name,
                "title": title,
                "performer": program.get("performer", ""),
                "description": (program.get("description") or "")[:500],
                "start_time": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "ft_dt": start_dt,
                "to_dt": end_dt,
                "stream_type": "b",
                "duration_sec": duration_sec,
            }
            try:
                stream_url, headers = progs.get_timefree_playback_source(stid, start_dt, end_dt)
                rm.play_timefree(
                    stream_url,
                    station_id=stid,
                    announce_text=announce,
                    headers=headers,
                    resume_seconds=0,
                    timefree_info=timefree_info,
                )
            except Exception as e1:
                self.log.warning(f"timefree playback primary failed: {e1}")
                stream_url, headers = progs.get_timefree_playback_source_compat(stid, start_dt, end_dt)
                rm.play_timefree(
                    stream_url,
                    station_id=stid,
                    announce_text=announce,
                    headers=headers,
                    resume_seconds=0,
                    timefree_info={**timefree_info, "stream_type": "c"},
                )
            if hasattr(mv, "program_info_handler"):
                mv.program_info_handler.show_timefree_program_info(timefree_info)
        except Exception as e:
            self.log.error(f"on_play_timefree: {e}")
            simpleDialog.errorDialog(_("聴き逃し再生に失敗しました。") + f"\n{e}")

    def on_record_timefree(self, event):
        try:
            index = self.result_list.GetFocusedItem()
            if index < 0:
                index = self.result_list.GetFirstSelected()
            if index < 0 or index >= len(self.search_results):
                simpleDialog.errorDialog(_("番組を選択してください。"))
                return
            program = self.search_results[index]
            start_dt, end_dt = self._program_start_end_dt(program)
            if not start_dt or not end_dt:
                simpleDialog.errorDialog(_("番組の日時を解釈できませんでした。"))
                return
            now = datetime.datetime.now()
            if start_dt > now:
                simpleDialog.errorDialog(_("未来の番組は聴き逃し録音できません。"))
                return
            if end_dt > now:
                simpleDialog.errorDialog(_("放送中の番組は聴き逃し録音できません。"))
                return
            stid = program.get("station_id")
            if not stid:
                simpleDialog.errorDialog(_("放送局情報がありません。"))
                return
            progs = getattr(globalVars.app.hMainView, "progs", None) or programmanager.ProgramManager()
            title = program.get("title", "")
            station_name = program.get("station_name", "")
            rh = getattr(globalVars.app.hMainView, "recording_handler", None)
            if rh and rh.stop_duplicate_program_recording_toggle(
                stid, title, announce_station_name=station_name or None
            ):
                return

            segments = progs.get_timefree_recording_segments(stid, start_dt, end_dt)
            filetype = get_file_type_from_config()
            safe_title = re.sub(r'[<>:"/\\|?*]', "_", title).strip()
            replace = safe_title.replace(" ", "-")
            station_dir = station_name.replace(" ", "_")
            dirs = create_recording_dir(station_dir, title)
            timestamp = start_dt.strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(dirs, f"{timestamp}_{replace}")
            info = f"{station_name} {title}"
            total_audio_sec = sum(s[2] for s in segments)
            end_time = time.time() + total_audio_sec + 600 + len(segments) * 60
            rec = recorder_manager.start_timefree_recording_segments(
                segments,
                output_path,
                info,
                end_time,
                filetype,
                station_id=stid,
                program_title=title,
                input_options=["-http_seekable", "0", "-seekable", "0"],
            )
            if rec:
                simpleDialog.dialog(_("完了"), _("聴き逃し録音を開始しました。") + f"\n{title}")
                try:
                    notification_notify(
                        title=_("録音開始"),
                        message=_("聴き逃し録音を開始しました。") + f"\n{title}",
                        app_name="rpb",
                        timeout=10,
                    )
                except Exception as e:
                    self.log.error(f"Failed to send timefree recording start notification: {e}")
            else:
                detail = recorder_manager.get_last_start_error()
                msg = _("聴き逃し録音の開始に失敗しました。")
                if detail:
                    msg += f"\n\n{detail}"
                simpleDialog.errorDialog(msg)
        except Exception as e:
            self.log.error(f"on_record_timefree: {e}")
            simpleDialog.errorDialog(_("聴き逃し録音に失敗しました。") + f"\n{e}")
    
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
            simpleDialog.errorDialog(_("番組詳細の表示に失敗しました。"))
    
    def onClear(self, event):
        """検索条件をクリア"""
        self.title_combo.SetValue("")
        self.performer_combo.SetValue("")
        self.station_combo.SetSelection(0)  # 「指定なし」を選択
        self._apply_date_options()
        self.date_combo.SetSelection(0)  # 「未来・放送中のみ」
        
        # スピンコントロールをリセット（ラジオ形式：5-29時、分は0分から）
        self.start_hour_spin.SetValue(self.DEFAULT_START_HOUR)
        self.start_minute_spin.SetValue(self.DEFAULT_START_MINUTE)
        self.end_hour_spin.SetValue(self.DEFAULT_END_HOUR)
        self.end_minute_spin.SetValue(self.DEFAULT_END_MINUTE)
        
        self.result_list.clear()
        self.result_count_label.SetLabel(_("検索結果: 0件"))
        self._update_recording_action_buttons(-1)

    def onStationChanged(self, event):
        """放送局が変更された時の処理"""
        # 必要に応じて実装
        pass
    
    def onDateChanged(self, event):
        """日付が変更された時の処理"""
        # 必要に応じて実装
        pass
    
    def onHistoryCheckboxChanged(self, event):
        """検索履歴チェックボックスが変更された時の処理"""
        self.history_enabled = self.history_checkbox.GetValue()
        
        if self.history_enabled:
            # 履歴を有効にした場合、履歴をコンボボックスに読み込み
            self.load_history_to_combos()
            self.history_clear_btn.Enable(True)
        else:
            # 履歴を無効にした場合、コンボボックスをクリア
            self.title_combo.Clear()
            self.performer_combo.Clear()
            self.history_clear_btn.Enable(False)
        
        self.log.debug(f"History enabled: {self.history_enabled}")
    
    def onTitleComboChanged(self, event):
        """番組タイトルコンボボックスが変更された時の処理"""
        pass
    
    def onPerformerComboChanged(self, event):
        """出演者コンボボックスが変更された時の処理"""
        pass
    
    def load_history_to_combos(self):
        """履歴をコンボボックスに読み込み"""
        if not self.history_enabled:
            return
        
        try:
            # 番組タイトル履歴を読み込み
            title_history = self.history_manager.get_title_history()
            self.title_combo.SetItems(title_history)
            
            # 出演者履歴を読み込み
            performer_history = self.history_manager.get_performer_history()
            self.performer_combo.SetItems(performer_history)
            
            self.log.debug(f"Loaded history: {len(title_history)} titles, {len(performer_history)} performers")
        except Exception as e:
            self.log.error(f"Failed to load history to combos: {e}")
    
    def onHistoryClear(self, event):
        """検索履歴をクリア"""
        try:
            # 確認ダイアログを表示
            result = simpleDialog.yesNoDialog(_("確認"), _("検索履歴をすべて削除しますか？"))
            if result == wx.ID_YES:
                self.history_manager.clear_history()
                
                # コンボボックスをクリア
                self.title_combo.Clear()
                self.performer_combo.Clear()
                
                simpleDialog.dialog(_("完了"), _("検索履歴を削除しました。"))
                self.log.info("Search history cleared by user")
        except Exception as e:
            self.log.error(f"Failed to clear history: {e}")
            simpleDialog.errorDialog(_("履歴の削除に失敗しました。"))
    
    def onRefresh(self, event):
        """データ更新"""
        try:
            if self._refresh_worker and self._refresh_worker.is_alive():
                simpleDialog.dialog(_("情報"), _("データ更新を実行中です。完了までお待ちください。"))
                return
            confirm_message = _(
                "検索に使用する番組データベースを更新します。\n"
                "更新にはしばらく時間がかかる場合があります。\n\n"
                "更新を開始しますか？"
            )
            result = simpleDialog.yesNoDialog(_("データ更新の確認"), confirm_message)
            if result != wx.ID_YES:
                return
            self._perform_data_refresh()
        except Exception as e:
            self.log.error(f"Operation failed: {e}")
            simpleDialog.errorDialog(_("操作中にエラーが発生しました。"))

    def _perform_data_refresh_worker(self, state):
        success = False
        partial = False
        error_text = None
        progress = state.get("progress")
        try:
            controller = getattr(globalVars.app.hMainView, 'program_cache_controller', None)
            if controller and hasattr(controller, 'force_weekly_update'):
                self.log.info("Requesting ProgramCacheController to force weekly update")
                success = controller.force_weekly_update(progress=progress)
        except Exception as e:
            self.log.warning(f"Controller update failed: {e}")
            error_text = str(e)

        # フォールバック: 最低限の当日データを収集（必要時のみ）
        if not success:
            try:
                if self.data_collector is None:
                    self.data_collector = ProgramDataCollector(self.cache_manager)
                    if self.radio_manager:
                        self.data_collector.set_radio_manager(self.radio_manager)
                self.log.warning("Falling back to today's data collection")
                if progress:
                    self.data_collector.set_progress_callback(progress.report)
                success = self.data_collector.collect_all_stations_data(force_refresh=True)
                partial = success
            except Exception as e:
                self.log.error(f"Fallback data collection failed: {e}")
                error_text = str(e)
            finally:
                if self.data_collector:
                    self.data_collector.set_progress_callback(None)

        state["success"] = success
        state["partial"] = partial
        state["error"] = error_text

    def _cleanup_refresh_ui(self):
        if self._refresh_poller:
            try:
                self._refresh_poller.Stop()
            except Exception:
                pass
        self._refresh_poller = None

    def _restore_focus_to_search_box(self):
        """更新完了後に検索ボックスへフォーカスを戻す。"""
        try:
            self.wnd.Raise()
            self.wnd.SetFocus()
            if hasattr(self, "title_combo") and self.title_combo:
                self.title_combo.SetFocus()
                if hasattr(self.title_combo, "SetInsertionPointEnd"):
                    self.title_combo.SetInsertionPointEnd()
                if hasattr(self.title_combo, "GetMainWindow"):
                    main = self.title_combo.GetMainWindow()
                    if main:
                        main.SetFocus()
        except Exception:
            pass

    def _poll_data_refresh_completion(self):
        if not self._refresh_worker:
            self._cleanup_refresh_ui()
            return

        if self._refresh_worker.is_alive():
            self._refresh_poller = wx.CallLater(200, self._poll_data_refresh_completion)
            return

        state = self._refresh_state or {"success": False}
        progress = state.get("progress")
        if progress:
            progress.finish(state.get("success", False))
        self._cleanup_refresh_ui()
        self._refresh_worker = None
        self._refresh_state = None

        if state.get("success"):
            partial = state.get("partial")
            if partial:
                simpleDialog.dialog(
                    _("完了"),
                    _("本日分の番組データのみ更新しました。")
                    + "\n"
                    + _("週間データの更新に失敗したため、過去・未来の日付は検索できない場合があります。"),
                )
            else:
                simpleDialog.dialog(_("完了"), _("データの更新が完了しました。"))
            self.update_station_list()
            wx.CallAfter(self._restore_focus_to_search_box)
            wx.CallLater(80, self._restore_focus_to_search_box)
            wx.CallLater(180, self._restore_focus_to_search_box)
            return

        error_text = state.get("error")
        if error_text:
            simpleDialog.errorDialog(_("データの更新に失敗しました。") + f"\n{error_text}")
        else:
            simpleDialog.errorDialog(_("データの更新に失敗しました。"))
        wx.CallAfter(self._restore_focus_to_search_box)
        wx.CallLater(80, self._restore_focus_to_search_box)
        wx.CallLater(180, self._restore_focus_to_search_box)

    def _perform_data_refresh(self):
        """データ更新の実際の処理"""
        progress = DatabaseUpdateProgress()
        progress.start(parent=None)
        self._refresh_state = {"success": False, "error": None, "progress": progress}
        self._refresh_worker = threading.Thread(
            target=self._perform_data_refresh_worker,
            args=(self._refresh_state,),
            daemon=True,
        )
        self._refresh_worker.start()
        self._poll_data_refresh_completion()
    
    def onScheduleRecording(self, event):
        """選択された番組を予約録音"""
        try:
            # 選択されたアイテムのインデックスを取得（フォーカスまたは選択されているアイテム）
            index = self.result_list.GetFocusedItem()
            if index < 0:
                # フォーカスされていない場合は、選択されている最初のアイテムを取得
                index = self.result_list.GetFirstSelected()
            
            if index < 0 or index >= len(self.search_results):
                simpleDialog.errorDialog(_("番組を選択してください。"))
                return
            
            program = self.search_results[index]
            
            # 必要な情報を取得
            station_id = program.get('station_id')
            station_name = program.get('station_name', '')
            program_title = program.get('title', '')
            start_time_str = program.get('start_time', '')
            end_time_str = program.get('end_time', '')
            date_str = program.get('date', '')
            
            if not all([station_id, program_title, start_time_str, end_time_str, date_str]):
                simpleDialog.errorDialog(_("番組情報が不完全です。"))
                self.log.error(f"Incomplete program data: {program}")
                return
            
            # 日付と時間をパース
            try:
                # 日付をパース（YYYYMMDD形式）
                if len(date_str) == 8:
                    year = int(date_str[:4])
                    month = int(date_str[4:6])
                    day = int(date_str[6:8])
                    selected_date = datetime.date(year, month, day)
                else:
                    raise ValueError(f"Invalid date format: {date_str}")
                
                start_time_dt = self._parse_clock_on_listing_date(selected_date, start_time_str)
                end_time_dt = self._parse_clock_on_listing_date(selected_date, end_time_str)
                if not start_time_dt or not end_time_dt:
                    raise ValueError(f"Invalid time format: {start_time_str} - {end_time_str}")

                start_time_dt = self._adjust_listing_datetime(
                    start_time_dt, start_time_str, is_end=False
                )
                end_time_dt = self._adjust_listing_datetime(
                    end_time_dt, end_time_str, is_end=True
                )
                if end_time_dt <= start_time_dt:
                    end_time_dt += datetime.timedelta(days=1)

                # 過去の番組かチェック
                current = datetime.datetime.now()
                if start_time_dt < current:
                    simpleDialog.errorDialog(_("過去の番組の録音はできません。"))
                    self.log.error(f"Failed to schedule program: Specified time ({start_time_dt}) is in the past.")
                    return
                
            except (ValueError, TypeError) as e:
                self.log.error(f"Failed to parse date/time: {e}")
                simpleDialog.errorDialog(_("日時情報の解析に失敗しました。"))
                return
            
            # 録音品質を取得（メインウィンドウの設定から）
            filetype = "mp3"  # デフォルト
            try:
                if hasattr(globalVars.app.hMainView, 'menu'):
                    menu = globalVars.app.hMainView.menu
                    if hasattr(menu, 'hRecordingFileTypeMenu'):
                        if menu.hRecordingFileTypeMenu.IsChecked(constants.RECORDING_M4A):  # M4A
                            filetype = "m4a"
                        elif menu.hRecordingFileTypeMenu.IsChecked(constants.RECORDING_WAV):  # WAV
                            filetype = "wav"
            except Exception as e:
                self.log.warning(f"Failed to get recording file type, using default: {e}")
            
            # 出力パスを準備
            safe_title = re.sub(r'[<>:"/\\|?*]', '_', program_title).strip()
            replace = safe_title.replace(" ", "-")
            from recorder import create_recording_dir
            station_dir = station_name.replace(" ", "_")
            dirs = create_recording_dir(station_dir, program_title)
            
            # タイムスタンプを追加してファイル名重複を回避
            timestamp = start_time_dt.strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(dirs, f"{timestamp}_{replace}")
            
            # 録音予約を作成
            schedule = RecordingSchedule(
                station_id=station_id,
                station_name=station_name,
                program_title=program_title,
                start_time=start_time_dt,
                end_time=end_time_dt,
                output_path=output_path,
                filetype=filetype
            )
            
            # 予約を追加
            added = schedule_manager.add_schedule(schedule)
            if not added:
                simpleDialog.dialog(_("情報"), _("同一番組の予約が既に存在します。"))
                return
            
            # 監視を開始（初回のみ）
            schedule_manager.start_monitoring()
            
            # 現在のスケジュール数を取得
            total_schedules = len(schedule_manager.schedules)
            
            # 通知を表示
            if total_schedules == 1:
                message = f'録音がスケジュールされました。録音は、{start_time_dt.strftime("%Y-%m-%d %H:%M")}に開始されます。'
            else:
                message = f'録音がスケジュールされました。録音は、{start_time_dt.strftime("%Y-%m-%d %H:%M")}に開始されます。（{total_schedules}件の録音予約中）'
            
            try:
                notification_notify(
                    title='録音準備',
                    message=message,
                    app_name='rpb',
                    timeout=10
                )
                self.log.info(f"Recording schedule notification sent successfully: {program_title}")
            except Exception as e:
                self.log.error(f"Failed to send recording schedule notification: {e}")
            
            self.log.info(f"Recording scheduled successfully: {program_title}")
            simpleDialog.dialog(_("完了"), message)
            
        except Exception as e:
            self.log.error(f"Error in onScheduleRecording: {e}")
            simpleDialog.errorDialog(f"録音スケジュールに失敗しました: {e}")
    
    def onClose(self, event):
        """ダイアログを閉じる"""
        self.Destroy()
    
    def setup_history_initial_state(self):
        """履歴の初期状態を設定"""
        try:
            # 履歴が存在するかチェック
            has_history = self.history_manager.has_history()
            
            if has_history:
                # 履歴が存在する場合、チェックボックスを有効にする
                self.history_checkbox.SetValue(True)
                self.history_enabled = True
                self.history_clear_btn.Enable(True)
                
                # 履歴をコンボボックスに読み込み
                self.load_history_to_combos()
                
                self.log.info("History found, enabling history features")
            else:
                # 履歴が存在しない場合、チェックボックスは無効のまま
                self.history_checkbox.SetValue(False)
                self.history_enabled = False
                self.history_clear_btn.Enable(False)
                
                self.log.info("No history found, history features disabled")
        except Exception as e:
            self.log.error(f"Failed to setup history initial state: {e}")
            # エラーの場合は履歴機能を無効にする
            self.history_checkbox.SetValue(False)
            self.history_enabled = False
            self.history_clear_btn.Enable(False)
    
    def cleanup(self):
        """リソースのクリーンアップ"""
        if self.data_collector:
            self.data_collector.cleanup()
        if self.cache_manager:
            self.cache_manager.close()
