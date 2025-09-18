# -*- coding: utf-8 -*-
# 番組キャッシュ制御モジュール

import os
import datetime
import threading
import sqlite3
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
        """データベースの存在チェックと更新（今日基準）"""
        try:
            if not self._validate_database_integrity():
                self.log.warning("Database integrity check failed, recreating database")
                self._create_fresh_database()
                return
            
            self.cache_manager = ProgramCacheManager(self.db_path)
            self._set_last_update_date()
            
            if self._needs_database_update():
                self._perform_database_update()
            else:
                self.log.info("Weekly cache is complete, using existing database")
                self._initialize_services()
            
        except Exception as e:
            self.log.error(f"Failed to check database: {e}")
            self._handle_database_error(e)
    
    def _set_last_update_date(self):
        """最終更新日を設定"""
        last_update_str = self.cache_manager.get_last_update_time()
        if last_update_str and len(last_update_str) >= 8:
            self.last_update_date = last_update_str[:8]
            self.log.info(f"Last update date: {self.last_update_date}")
        else:
            self.last_update_date = None
            self.log.info("No previous update date found")
    
    def _needs_database_update(self):
        """データベース更新が必要かチェック"""
        today_date = datetime.datetime.now().strftime('%Y%m%d')
        is_weekly_complete = self.cache_manager.is_weekly_cache_complete()
        
        return (
            self.last_update_date is None or
            self.last_update_date != today_date or
            not is_weekly_complete
        )
    
    def _perform_database_update(self):
        """データベース更新を実行"""
        today_date = datetime.datetime.now().strftime('%Y%m%d')
        is_weekly_complete = self.cache_manager.is_weekly_cache_complete()
        
        reasons = []
        if self.last_update_date is None:
            reasons.append("初回起動")
        if self.last_update_date != today_date:
            reasons.append(f"基準日変更 ({self.last_update_date} → {today_date})")
        if not is_weekly_complete:
            reasons.append("週間データ不完全")
        
        self.log.info(f"Database update needed: {', '.join(reasons)}")
        self._update_database()
    
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
        """データベースを更新（1週間分のデータを取得）"""
        try:
            if not self.radio_manager:
                self.log.warning("RadioManager not available, skipping database update")
                self._initialize_services()
                return
            
            # 放送局リストが初期化されているかチェック
            if not hasattr(self.radio_manager, 'stid') or not self.radio_manager.stid:
                self.log.warning("Radio station list not initialized. Scheduling background update after area determination.")
                self._initialize_services()
                self._schedule_wait_and_update()
                return
            
            self.log.info(f"Starting database update with {len(self.radio_manager.stid)} stations")
            
            # データ収集器を初期化
            self.data_collector = ProgramDataCollector(self.cache_manager)
            self.data_collector.set_radio_manager(self.radio_manager)
            
            # 今日から1週間分のデータを効率的に収集
            today = datetime.datetime.now()
            today = today.replace(hour=0, minute=0, second=0, microsecond=0)
            
            self.log.info(f"Starting weekly data collection from {today.strftime('%Y%m%d')}")
            
            # 週間データ収集を実行
            success = self.data_collector.collect_weekly_data(today, force_refresh=True)
            
            if success:
                self.log.info("Weekly database update completed successfully")
                self.last_update_date = self.startup_date
            else:
                self.log.warning("Weekly database update failed, but continuing with existing data")
            
            # 古いデータをクリーンアップ（14日以上古いデータを削除）
            self.cache_manager.cleanup_old_data(days=14)
            
            # サービスを初期化
            self._initialize_services()
            
        except Exception as e:
            self.log.error(f"Failed to update database: {e}")
            self._handle_database_error(e)
    
    def force_weekly_update(self):
        """1週間分のデータを強制的に更新"""
        try:
            if not self.radio_manager or not hasattr(self.radio_manager, 'stid') or not self.radio_manager.stid:
                self.log.error("RadioManager or station list not available for weekly update")
                return False
            
            self.log.info("Starting forced weekly data update")
            
            # データ収集器を初期化
            if not self.data_collector:
                self.data_collector = ProgramDataCollector(self.cache_manager)
                self.data_collector.set_radio_manager(self.radio_manager)
            
            # 今日から1週間分のデータを効率的に収集
            today = datetime.datetime.now()
            today = today.replace(hour=0, minute=0, second=0, microsecond=0)
            
            self.log.info(f"Starting forced weekly data collection from {today.strftime('%Y%m%d')}")
            
            # 週間データ収集を実行
            success = self.data_collector.collect_weekly_data(today, force_refresh=True)
            
            if success:
                self.log.info("Forced weekly database update completed successfully")
                self.last_update_date = self.startup_date
                return True
            else:
                self.log.warning("Forced weekly database update failed")
                return False
            
        except Exception as e:
            self.log.error(f"Failed to force weekly update: {e}")
            return False
    
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
    
    def ensure_weekly_data(self):
        """1週間分のデータが存在することを保証"""
        try:
            if not self.radio_manager or not hasattr(self.radio_manager, 'stid') or not self.radio_manager.stid:
                self.log.warning("RadioManager not available for weekly data check")
                return False
            
            # 1週間分のキャッシュが完全かチェック
            if self.cache_manager and self.cache_manager.is_weekly_cache_complete():
                self.log.info("Weekly cache is already complete")
                return True
            
            # 不完全な場合は強制更新
            self.log.info("Weekly cache is incomplete, forcing update")
            return self.force_weekly_update()
            
        except Exception as e:
            self.log.error(f"Failed to ensure weekly data: {e}")
            return False
    
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
    
    def _validate_database_integrity(self):
        """データベースの整合性を検証（削除・改変・接続失敗の検出）"""
        try:
            # データベースファイルの存在チェック
            if not os.path.exists(self.db_path):
                self.log.warning("Database file does not exist")
                return False
            
            # ファイルサイズチェック（空ファイルや破損ファイルの検出）
            file_size = os.path.getsize(self.db_path)
            if file_size == 0:
                self.log.warning("Database file is empty")
                return False
            
            # SQLiteデータベースとして開けるかチェック
            try:
                test_conn = sqlite3.connect(self.db_path, check_same_thread=False)
                cursor = test_conn.cursor()
                
                # 基本的なテーブル構造の存在チェック
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]
                
                required_tables = ['programs', 'cache_metadata']
                missing_tables = [table for table in required_tables if table not in tables]
                
                if missing_tables:
                    self.log.warning(f"Missing required tables: {missing_tables}")
                    test_conn.close()
                    return False
                
                # テーブル構造の整合性チェック
                cursor.execute("PRAGMA table_info(programs)")
                programs_columns = [row[1] for row in cursor.fetchall()]
                required_columns = ['station_id', 'station_name', 'title', 'performer', 'start_time', 'end_time', 'description', 'date']
                missing_columns = [col for col in required_columns if col not in programs_columns]
                
                if missing_columns:
                    self.log.warning(f"Missing required columns in programs table: {missing_columns}")
                    test_conn.close()
                    return False
                
                # データベースの整合性チェック
                cursor.execute("PRAGMA integrity_check")
                integrity_result = cursor.fetchone()
                if integrity_result and integrity_result[0] != 'ok':
                    self.log.warning(f"Database integrity check failed: {integrity_result[0]}")
                    test_conn.close()
                    return False
                
                test_conn.close()
                self.log.info("Database integrity validation passed")
                return True
                
            except sqlite3.Error as e:
                self.log.warning(f"Database connection/validation failed: {e}")
                return False
                
        except Exception as e:
            self.log.warning(f"Database integrity check failed with exception: {e}")
            return False

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

    def _schedule_wait_and_update(self):
        """放送局リストが利用可能になるまで待機してから週間更新を実行"""
        try:
            def wait_and_update():
                try:
                    self.log.info("Waiting for station list to be initialized...")
                    for _ in range(60):  # 最大約60秒待機
                        try:
                            if hasattr(self.radio_manager, 'stid') and self.radio_manager.stid:
                                break
                        except Exception:
                            pass
                        import time
                        time.sleep(1)
                    if not getattr(self.radio_manager, 'stid', None):
                        self.log.warning("Station list still not available. Aborting weekly update.")
                        return
                    # 収集器を用意
                    if not self.data_collector:
                        self.data_collector = ProgramDataCollector(self.cache_manager)
                        self.data_collector.set_radio_manager(self.radio_manager)
                    today = datetime.datetime.now()
                    today = today.replace(hour=0, minute=0, second=0, microsecond=0)
                    self.log.info(f"Starting deferred weekly data collection from {today.strftime('%Y%m%d')}")
                    ok = self.data_collector.collect_weekly_data(today, force_refresh=True)
                    if ok:
                        self.log.info("Deferred weekly database update completed successfully")
                        self.last_update_date = self.startup_date
                    else:
                        self.log.warning("Deferred weekly database update failed")
                except Exception as e:
                    self.log.error(f"Deferred update failed: {e}")
            t = threading.Thread(target=wait_and_update, daemon=True)
            t.start()
        except Exception as e:
            self.log.error(f"Failed to schedule wait-and-update: {e}")
    
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
