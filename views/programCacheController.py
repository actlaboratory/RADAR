# -*- coding: utf-8 -*-
# 番組キャッシュ制御モジュール

import os
import datetime
import threading
from logging import getLogger
import constants
import globalVars
from views.programCacheManager import ProgramCacheManager
from views.programDataCollector import ProgramDataCollector
from views.programSearchEngine import ProgramSearchEngine

class ProgramCacheController:
    """番組キャッシュの制御クラス（起動時チェック・例外処理）"""
    
    def __init__(self, radio_manager=None):
        self.log = getLogger(f"{constants.LOG_PREFIX}.ProgramCacheController")
        self.radio_manager = radio_manager
        
        # キャッシュ関連の初期化
        self.cache_manager = None
        self.data_collector = None
        self.search_engine = None
        
        # 起動時の日付を記録
        self.startup_date = datetime.datetime.now().strftime('%Y%m%d')
        self.last_update_date = None
        
        # データベースファイルのパス
        self.db_path = constants.PROGRAM_CACHE_DB_NAME
        
        # 初期化を実行
        self._initialize_cache_system()
    
    def _initialize_cache_system(self):
        """キャッシュシステムの初期化"""
        try:
            # データベースファイルの存在チェック
            if os.path.exists(self.db_path):
                self.log.info(f"Database file found: {self.db_path}")
                self._check_and_update_database()
            else:
                self.log.info("Database file not found, creating new one")
                self._create_fresh_database()
            
        except Exception as e:
            self.log.error(f"Failed to initialize cache system: {e}")
            self._handle_database_error(e)
    
    def _check_and_update_database(self):
        """データベースの存在チェックと更新"""
        try:
            # キャッシュマネージャーを初期化
            self.cache_manager = ProgramCacheManager(self.db_path)
            
            # 最終更新日を取得
            last_update_str = self.cache_manager.get_last_update_time()
            if last_update_str:
                self.last_update_date = last_update_str[:8]  # YYYYMMDD部分のみ
                self.log.info(f"Last update date: {self.last_update_date}")
            else:
                self.last_update_date = None
                self.log.info("No previous update date found")
            
            # 日付が変わっているかチェック
            if self.last_update_date != self.startup_date:
                self.log.info(f"Date changed from {self.last_update_date} to {self.startup_date}, updating database")
                self._update_database()
            else:
                self.log.info("Date unchanged, using existing database")
                self._initialize_services()
            
        except Exception as e:
            self.log.error(f"Failed to check database: {e}")
            self._handle_database_error(e)
    
    def _create_fresh_database(self):
        """新しいデータベースを作成"""
        try:
            self.log.info("Creating fresh database")
            
            # 既存のデータベースファイルを削除（存在する場合）
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
                self.log.info("Removed existing database file")
            
            # 新しいデータベースを作成
            self.cache_manager = ProgramCacheManager(self.db_path)
            self._update_database()
            
        except Exception as e:
            self.log.error(f"Failed to create fresh database: {e}")
            self._handle_database_error(e)
    
    def _update_database(self):
        """データベースを更新"""
        try:
            if not self.radio_manager:
                self.log.warning("RadioManager not available, skipping database update")
                self._initialize_services()
                return
            
            # 放送局リストが初期化されているかチェック
            if not hasattr(self.radio_manager, 'stid') or not self.radio_manager.stid:
                self.log.warning("Radio station list not initialized, skipping database update")
                self._initialize_services()
                return
            
            self.log.info(f"Starting database update with {len(self.radio_manager.stid)} stations")
            
            # データ収集器を初期化
            self.data_collector = ProgramDataCollector(self.cache_manager)
            self.data_collector.set_radio_manager(self.radio_manager)
            
            # 今日と明日のデータを収集
            today = datetime.datetime.now().strftime('%Y%m%d')
            tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime('%Y%m%d')
            
            # データ収集を実行
            success_today = self.data_collector.collect_all_stations_data(today, force_refresh=True)
            success_tomorrow = self.data_collector.collect_all_stations_data(tomorrow, force_refresh=True)
            
            if success_today or success_tomorrow:
                self.log.info("Database update completed successfully")
                self.last_update_date = self.startup_date
            else:
                self.log.warning("Database update failed, but continuing with existing data")
            
            # 古いデータをクリーンアップ
            self.cache_manager.cleanup_old_data(days=7)
            
            # サービスを初期化
            self._initialize_services()
            
        except Exception as e:
            self.log.error(f"Failed to update database: {e}")
            self._handle_database_error(e)
    
    def _initialize_services(self):
        """検索サービスを初期化"""
        try:
            if not self.cache_manager:
                self.cache_manager = ProgramCacheManager(self.db_path)
            
            self.search_engine = ProgramSearchEngine(self.cache_manager)
            
            if not self.data_collector:
                self.data_collector = ProgramDataCollector(self.cache_manager)
                if self.radio_manager:
                    self.data_collector.set_radio_manager(self.radio_manager)
            
            self.log.info("Cache services initialized successfully")
            
        except Exception as e:
            self.log.error(f"Failed to initialize services: {e}")
            self._handle_database_error(e)
    
    def _handle_database_error(self, error):
        """データベースエラーの処理"""
        self.log.error(f"Handling database error: {error}")
        
        try:
            # データベースファイルを完全に削除
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
                self.log.info("Database file removed due to error")
            
            # 新しいデータベースを作成
            self.cache_manager = ProgramCacheManager(self.db_path)
            self.search_engine = ProgramSearchEngine(self.cache_manager)
            
            if self.radio_manager:
                self.data_collector = ProgramDataCollector(self.cache_manager)
                self.data_collector.set_radio_manager(self.radio_manager)
                
                # バックグラウンドでデータ収集を試行
                self._schedule_background_update()
            
            self.log.info("Database reset completed")
            
        except Exception as reset_error:
            self.log.error(f"Failed to reset database: {reset_error}")
            # 最後の手段として、空のサービスを提供
            self._create_empty_services()
    
    def handle_critical_error(self, error):
        """致命的エラーの処理（データベースを完全にリセット）"""
        self.log.critical(f"Critical error occurred: {error}")
        
        try:
            # データベースファイルを削除
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
                self.log.info("Database file removed due to critical error")
            
            # 関連ファイルも削除
            backup_files = [f"{self.db_path}.backup", f"{self.db_path}.old"]
            for backup_file in backup_files:
                if os.path.exists(backup_file):
                    os.remove(backup_file)
                    self.log.info(f"Removed backup file: {backup_file}")
            
            # 新しいデータベースを作成
            self.cache_manager = ProgramCacheManager(self.db_path)
            self.search_engine = ProgramSearchEngine(self.cache_manager)
            
            if self.radio_manager:
                self.data_collector = ProgramDataCollector(self.cache_manager)
                self.data_collector.set_radio_manager(self.radio_manager)
            
            self.log.info("Critical error recovery completed")
            
        except Exception as recovery_error:
            self.log.critical(f"Failed to recover from critical error: {recovery_error}")
            # 完全に無効化
            self.cache_manager = None
            self.search_engine = None
            self.data_collector = None
    
    def _create_empty_services(self):
        """空のサービスを作成（最後の手段）"""
        try:
            self.cache_manager = ProgramCacheManager(self.db_path)
            self.search_engine = ProgramSearchEngine(self.cache_manager)
            self.data_collector = ProgramDataCollector(self.cache_manager)
            
            if self.radio_manager:
                self.data_collector.set_radio_manager(self.radio_manager)
            
            self.log.warning("Created empty services as fallback")
            
        except Exception as e:
            self.log.error(f"Failed to create empty services: {e}")
    
    def _schedule_background_update(self):
        """バックグラウンドでのデータ更新をスケジュール"""
        try:
            def background_update():
                try:
                    self.log.info("Starting background database update")
                    today = datetime.datetime.now().strftime('%Y%m%d')
                    success = self.data_collector.collect_all_stations_data(today, force_refresh=True)
                    
                    if success:
                        self.log.info("Background update completed successfully")
                    else:
                        self.log.warning("Background update failed")
                        
                except Exception as e:
                    self.log.error(f"Background update failed: {e}")
            
            # バックグラウンドスレッドで実行
            update_thread = threading.Thread(target=background_update, daemon=True)
            update_thread.start()
            
        except Exception as e:
            self.log.error(f"Failed to schedule background update: {e}")
    
    def get_search_engine(self):
        """検索エンジンを取得"""
        return self.search_engine
    
    def get_data_collector(self):
        """データ収集器を取得"""
        return self.data_collector
    
    def get_cache_manager(self):
        """キャッシュマネージャーを取得"""
        return self.cache_manager
    
    def force_update(self):
        """強制的にデータベースを更新"""
        try:
            self.log.info("Force update requested")
            self._update_database()
            return True
        except Exception as e:
            self.log.error(f"Force update failed: {e}")
            return False
    
    def get_database_status(self):
        """データベースの状態を取得"""
        try:
            if not self.cache_manager:
                return {
                    'status': 'not_initialized',
                    'last_update': None,
                    'program_count': 0,
                    'database_exists': os.path.exists(self.db_path)
                }
            
            last_update = self.cache_manager.get_last_update_time()
            program_count = self.cache_manager.get_program_count()
            
            return {
                'status': 'initialized',
                'last_update': last_update,
                'program_count': program_count,
                'database_exists': os.path.exists(self.db_path),
                'startup_date': self.startup_date,
                'last_update_date': self.last_update_date
            }
            
        except Exception as e:
            self.log.error(f"Failed to get database status: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'database_exists': os.path.exists(self.db_path)
            }
    
    def cleanup(self):
        """リソースのクリーンアップ"""
        try:
            if self.data_collector:
                self.data_collector.cleanup()
            
            if self.cache_manager:
                self.cache_manager.close()
            
            self.log.info("ProgramCacheController cleanup completed")
            
        except Exception as e:
            self.log.error(f"Cleanup failed: {e}")
    
    def __del__(self):
        """デストラクタ"""
        self.cleanup()
