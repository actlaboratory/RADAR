# -*- coding: utf-8 -*-
# 番組データ収集モジュール

import threading
import datetime
import time
from logging import getLogger
import constants
from views import programmanager
from views import radioManager
from views.programCacheManager import ProgramCacheManager

class ProgramDataCollector:
    """全放送局の番組データを収集・管理するクラス"""
    
    def __init__(self, cache_manager=None):
        self.log = getLogger(f"{constants.LOG_PREFIX}.ProgramDataCollector")
        self.cache_manager = cache_manager or ProgramCacheManager()
        self.program_manager = programmanager.ProgramManager()
        self.radio_manager = None  # 後で設定
        self.collection_thread = None
        self.is_collecting = False
        self.collection_interval = 3600  # 1時間ごと
    
    def set_radio_manager(self, radio_manager):
        """RadioManagerを設定"""
        self.radio_manager = radio_manager
    
    def collect_all_stations_data(self, date=None, force_refresh=False):
        """全放送局の番組データを収集"""
        if not self.radio_manager:
            self.log.error("RadioManager not set")
            return False
        
        if date is None:
            # ラジオの日付ルールに従った日付を取得
            from tcutil import CalendarUtil
            calendar_util = CalendarUtil()
            date = calendar_util.get_radio_date()
        
        # キャッシュの有効性をチェック
        if not force_refresh and self.cache_manager.is_cache_valid(date):
            self.log.info(f"Cache is valid for date {date}, skipping collection")
            return True
        
        self.log.info(f"Starting data collection for date {date}")
        
        try:
            # 全放送局のIDを取得
            if not self.radio_manager or not hasattr(self.radio_manager, 'stid') or not self.radio_manager.stid:
                self.log.error("RadioManager or station list not available")
                return False
                
            station_ids = list(self.radio_manager.stid.keys())
            self.log.info(f"Found {len(station_ids)} stations to collect: {station_ids[:5]}...")  # 最初の5つを表示
            
            # 各放送局のデータを収集
            collected_data = {}
            success_count = 0
            
            for station_id in station_ids:
                try:
                    station_data = self._collect_station_data(station_id, date)
                    if station_data:
                        collected_data[station_id] = station_data
                        success_count += 1
                        self.log.debug(f"Collected data for station {station_id}")
                    
                    # API制限を考慮して少し待機
                    time.sleep(0.1)
                    
                except Exception as e:
                    self.log.warning(f"Failed to collect data for station {station_id}: {e}")
                    continue
            
            # データをキャッシュに保存
            if collected_data:
                self.cache_manager.update_programs_data(collected_data, date)
                self.log.info(f"Successfully collected data for {success_count}/{len(station_ids)} stations")
                return True
            else:
                self.log.error("No data collected")
                return False
                
        except Exception as e:
            self.log.error(f"Data collection failed: {e}")
            return False
    
    def collect_weekly_data(self, start_date=None, force_refresh=False):
        """1週間分のデータを効率的に収集"""
        if not self.radio_manager:
            self.log.error("RadioManager not set")
            return False
        
        if start_date is None:
            # 今日（0:00:00）を基準にする
            today = datetime.datetime.now()
            start_date = today.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # 1週間分の日付リストを生成
        date_list = []
        for i in range(7):
            target_date = start_date + datetime.timedelta(days=i)
            date_list.append(target_date.strftime('%Y%m%d'))
        
        self.log.info(f"Starting weekly data collection from {date_list[0]} to {date_list[-1]}")
        
        # 全放送局のIDを取得
        if not self.radio_manager or not hasattr(self.radio_manager, 'stid') or not self.radio_manager.stid:
            self.log.error("RadioManager or station list not available")
            return False
        
        station_ids = list(self.radio_manager.stid.keys())
        self.log.info(f"Collecting data for {len(station_ids)} stations across 7 days")
        
        success_count = 0
        total_days = len(date_list)
        
        for date_str in date_list:
            try:
                self.log.info(f"Collecting data for date: {date_str}")
                
                # 各日付のデータを収集
                collected_data = {}
                day_success_count = 0
                
                for station_id in station_ids:
                    try:
                        station_data = self._collect_station_data(station_id, date_str)
                        if station_data:
                            collected_data[station_id] = station_data
                            day_success_count += 1
                        
                        # API制限を考慮して少し待機
                        time.sleep(0.05)  # 週間収集では少し短縮
                        
                    except Exception as e:
                        self.log.warning(f"Failed to collect data for station {station_id} on {date_str}: {e}")
                        continue
                
                # データをキャッシュに保存
                if collected_data:
                    self.cache_manager.update_programs_data(collected_data, date_str)
                    self.log.info(f"Successfully collected data for {day_success_count}/{len(station_ids)} stations on {date_str}")
                    success_count += 1
                else:
                    self.log.warning(f"No data collected for date {date_str}")
                
            except Exception as e:
                self.log.error(f"Error collecting data for date {date_str}: {e}")
                continue
        
        if success_count > 0:
            self.log.info(f"Weekly data collection completed: {success_count}/{total_days} days successful")
            return True
        else:
            self.log.error("Weekly data collection failed for all dates")
            return False
    
    def _collect_station_data(self, station_id, date):
        """単一放送局のデータを収集"""
        try:
            # 既存のProgramManagerを使用して番組データを取得
            self.program_manager.retrieveRadioListings(station_id, date)
            
            # 番組情報を取得（安全に取得）
            try:
                titles = self.program_manager.gettitle() or []
            except Exception as e:
                self.log.warning(f"Failed to get titles for station {station_id}: {e}")
                titles = []
            
            try:
                performers = self.program_manager.getpfm() or []
            except Exception as e:
                self.log.warning(f"Failed to get performers for station {station_id}: {e}")
                performers = []
            
            try:
                start_times = self.program_manager.get_ftl() or []
            except Exception as e:
                self.log.warning(f"Failed to get start times for station {station_id}: {e}")
                start_times = []
            
            try:
                end_times = self.program_manager.get_tol() or []
            except Exception as e:
                self.log.warning(f"Failed to get end times for station {station_id}: {e}")
                end_times = []
            
            try:
                descriptions = self.program_manager.getDescriptions() or []
            except Exception as e:
                self.log.warning(f"Failed to get descriptions for station {station_id}: {e}")
                descriptions = []
            
            # データの長さを確認
            max_length = max(len(titles), len(performers), len(start_times), len(end_times), len(descriptions))
            self.log.debug(f"Station {station_id}: titles={len(titles)}, performers={len(performers)}, start_times={len(start_times)}, end_times={len(end_times)}, descriptions={len(descriptions)}, max_length={max_length}")
            
            # データを整理（安全にアクセス）
            programs = []
            for i in range(max_length):
                try:
                    # 各配列から安全に値を取得
                    title = titles[i] if i < len(titles) and titles[i] else ''
                    performer = performers[i] if i < len(performers) and performers[i] else ''
                    start_time = start_times[i] if i < len(start_times) and start_times[i] else ''
                    end_time = end_times[i] if i < len(end_times) and end_times[i] else ''
                    description = descriptions[i] if i < len(descriptions) and descriptions[i] else ''
                    
                    # タイトルが存在する場合のみ番組として追加
                    if title and title.strip():
                        program = {
                            'title': title.strip(),
                            'performer': performer.strip() if performer else '',
                            'start_time': self._format_time(start_time) if start_time else '',
                            'end_time': self._format_time(end_time) if end_time else '',
                            'description': description.strip() if description else ''
                        }
                        programs.append(program)
                        
                except Exception as e:
                    self.log.warning(f"Failed to process program data at index {i} for station {station_id}: {e}")
                    continue
            
            # 放送局情報を取得
            station_name = ''
            if self.radio_manager and hasattr(self.radio_manager, 'stid'):
                station_name = self.radio_manager.stid.get(station_id, '')
            
            self.log.debug(f"Collected {len(programs)} programs for station {station_id}")
            
            return {
                'name': station_name,
                'programs': programs
            }
            
        except Exception as e:
            self.log.error(f"Failed to collect data for station {station_id}: {e}")
            import traceback
            self.log.error(f"Traceback: {traceback.format_exc()}")
            return None
    
    def _format_time(self, time_str):
        """時間文字列をフォーマット"""
        if not time_str or len(time_str) < 4:
            return ''
        
        # HHMM形式をHH:MM:SS形式に変換
        if len(time_str) == 4:
            return f"{time_str[:2]}:{time_str[2:4]}:00"
        return time_str
    
    def start_background_collection(self):
        """バックグラウンドでの定期データ収集を開始"""
        if self.is_collecting:
            self.log.warning("Background collection already running")
            return
        
        self.is_collecting = True
        self.collection_thread = threading.Thread(target=self._background_collection_loop, daemon=True)
        self.collection_thread.start()
        self.log.info("Background collection started")
    
    def stop_background_collection(self):
        """バックグラウンド収集を停止"""
        self.is_collecting = False
        if self.collection_thread:
            self.collection_thread.join(timeout=5)
        self.log.info("Background collection stopped")
    
    def _background_collection_loop(self):
        """バックグラウンド収集ループ"""
        while self.is_collecting:
            try:
                # ラジオの日付ルールに従った日付を取得
                from tcutil import CalendarUtil
                calendar_util = CalendarUtil()
                today = calendar_util.get_radio_date()
                tomorrow = (datetime.datetime.strptime(today, '%Y%m%d') + datetime.timedelta(days=1)).strftime('%Y%m%d')
                
                self.log.debug(f"Collecting data for today: {today}, tomorrow: {tomorrow}")
                
                self.collect_all_stations_data(today)
                self.collect_all_stations_data(tomorrow)
                
                # 古いデータをクリーンアップ
                self.cache_manager.cleanup_old_data(days=7)
                
                # 次の収集まで待機
                time.sleep(self.collection_interval)
                
            except Exception as e:
                self.log.error(f"Background collection error: {e}")
                time.sleep(60)  # エラー時は1分待機
    
    def get_available_dates(self):
        """利用可能な日付のリストを取得"""
        try:
            cursor = self.cache_manager.conn.cursor()
            cursor.execute("SELECT DISTINCT date FROM programs ORDER BY date DESC")
            dates = [row[0] for row in cursor.fetchall()]
            return dates
        except Exception as e:
            self.log.error(f"Failed to get available dates: {e}")
            return []
    
    def get_station_list(self):
        """収集済みの放送局リストを取得"""
        try:
            cursor = self.cache_manager.conn.cursor()
            cursor.execute("SELECT DISTINCT station_id, station_name FROM programs ORDER BY station_name")
            stations = [(row[0], row[1]) for row in cursor.fetchall()]
            return stations
        except Exception as e:
            self.log.error(f"Failed to get station list: {e}")
            return []
    
    def cleanup(self):
        """リソースのクリーンアップ"""
        self.stop_background_collection()
        if self.cache_manager:
            self.cache_manager.close()
        self.log.info("ProgramDataCollector cleanup completed")
