# -*- coding: utf-8 -*-
# 番組キャッシュ制御モジュール

import os
import datetime
import threading
import sqlite3
from logging import getLogger
import constants
import globalVars
from notification_util import notify as notification_notify
from tcutil import CalendarUtil
from views import premium
from views import programDataCollector
from views.programCacheManager import ProgramCacheManager
from views.programDataCollector import ProgramDataCollector
from views.programSearchEngine import ProgramSearchEngine

# キャッシュがどの放送局範囲で収集されたか（"area": 自エリアのみ / "areafree": 全国）
STATION_SCOPE_KEY = "station_scope"
STATION_SCOPE_AREA = "area"
STATION_SCOPE_AREAFREE = "areafree"

# バックグラウンド収集が打ち切り・一部失敗で終わったときに自動で再試行する回数と、
# 再試行までの待ち時間（収集器の打ち切り後の待機が明けてから始める）
MAX_AUTO_RETRIES = 3
AUTO_RETRY_DELAY_SEC = programDataCollector.ABORT_COOLDOWN_SEC + 5

class ProgramCacheController:
    """番組キャッシュの制御クラス（起動時チェック・例外処理）"""

    def __init__(self, radio_manager=None):
        self.log = getLogger(f"{constants.LOG_PREFIX}.ProgramCacheController")
        self.radio_manager = radio_manager
        
        # キャッシュ関連の初期化
        self.cache_manager = None
        self.data_collector = None
        self.search_engine = None
        
        # 起動時のラジオ基準日を記録
        self.startup_date = CalendarUtil().get_radio_date()
        self.last_update_date = None
        
        # データベースファイルのパス
        self.db_path = constants.PROGRAM_CACHE_DB_NAME
        self._cleanup_done = False
        # 週間収集の同時実行を防ぐ（ログイン直後の再収集と起動時収集が重ならないように）
        self._weekly_collect_lock = threading.Lock()
        # バックグラウンド収集の自動再試行（何回目か・待機中のタイマー）
        self._retry_count = 0
        self._retry_timer = None
        
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
        today_date = CalendarUtil().get_radio_date()
        is_weekly_complete = self.cache_manager.is_weekly_cache_complete()
        
        return (
            self.last_update_date is None or
            self.last_update_date != today_date or
            not is_weekly_complete or
            self._station_scope_mismatch()
        )

    @staticmethod
    def _current_station_scope():
        return STATION_SCOPE_AREAFREE if premium.session.is_areafree() else STATION_SCOPE_AREA

    def _station_scope_mismatch(self):
        """キャッシュの収集範囲が現在のログイン状態と一致しないか"""
        if not self.cache_manager:
            return False
        stored = self.cache_manager.get_metadata(STATION_SCOPE_KEY) or STATION_SCOPE_AREA
        return stored != self._current_station_scope()

    def on_station_scope_changed(self):
        """ログイン/ログアウトで放送局一覧が変わったときに呼ぶ。

        キャッシュの収集範囲が変わる場合はバックグラウンドで再収集し、True を返す。
        """
        if not self._station_scope_mismatch():
            return False
        self.log.info(f"Station scope changed to {self._current_station_scope()}; recollecting program cache")
        if not self.data_collector:
            self._initialize_services()
        self._schedule_weekly_update_background()
        return True
    
    def _perform_database_update(self):
        """データベース更新を実行"""
        today_date = CalendarUtil().get_radio_date()
        is_weekly_complete = self.cache_manager.is_weekly_cache_complete()
        
        reasons = []
        if self.last_update_date is None:
            reasons.append("初回起動")
        if self.last_update_date != today_date:
            reasons.append(f"基準日変更 ({self.last_update_date} → {today_date})")
        if not is_weekly_complete:
            reasons.append("週間データ不完全")
        if self._station_scope_mismatch():
            reasons.append(f"収集範囲変更 ({self._current_station_scope()})")
        
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
            
            self.log.info(f"Scheduling background database update with {len(self.radio_manager.stid)} stations")
            
            if not self.data_collector:
                self.data_collector = ProgramDataCollector(self.cache_manager)
                self.data_collector.set_radio_manager(self.radio_manager)
            
            self._initialize_services()
            self._schedule_weekly_update_background()
            
        except Exception as e:
            self.log.error(f"Failed to update database: {e}")
            self._handle_database_error(e)
    
    def force_weekly_update(self, progress=None, force_refresh=True):
        """1週間分のデータを強制的に更新（force_refresh が False なら収集済みの局は取得しない）"""
        try:
            if not self.radio_manager or not hasattr(self.radio_manager, 'stid') or not self.radio_manager.stid:
                self.log.error("RadioManager or station list not available for weekly update")
                return False
            
            self.log.info("Starting forced weekly data update")
            
            # データ収集器を初期化
            if not self.data_collector:
                self.data_collector = ProgramDataCollector(self.cache_manager)
                self.data_collector.set_radio_manager(self.radio_manager)
            
            radio_date = CalendarUtil().get_radio_date()
            self.log.info(f"Starting forced weekly data collection from radio base date {radio_date}")
            
            success = self._collect_weekly_data(progress=progress, force_refresh=force_refresh)
            
            if success:
                self.log.info("Forced weekly database update completed successfully")
                if self.cache_manager:
                    self.cache_manager.cleanup_old_data(days=21)
                return True
            else:
                self.log.warning("Forced weekly database update failed")
                return False
            
        except Exception as e:
            self.log.error(f"Failed to force weekly update: {e}")
            return False

    def _collect_weekly_data(self, progress=None, force_refresh=False):
        """週間データ収集を実行（progress 指定時のみ進捗UI）。

        progress が無いバックグラウンド収集では、開始・完了・失敗をアクションセンターに通知し、
        打ち切りや一部失敗で終わったら時間をおいて自動で再試行する。
        """
        success = False
        # 手動更新は進捗ダイアログに結果が出るので通知しない
        notify = progress is None
        with self._weekly_collect_lock:
            # 収集中にログイン状態が変わった場合は、次回のチェックで再収集されるよう開始時点の範囲を記録する
            scope = self._current_station_scope()
            try:
                if progress and self.data_collector:
                    self.data_collector.set_progress_callback(progress.report)
                success = self.data_collector.collect_weekly_data(
                    force_refresh=force_refresh,
                    on_start=self._notify_collection_started if notify else None,
                )
                if success:
                    self.last_update_date = CalendarUtil().get_radio_date()
                    if self.cache_manager:
                        self.cache_manager.set_metadata(STATION_SCOPE_KEY, scope)
                if notify:
                    self._handle_collection_outcome()
                return success
            finally:
                if self.data_collector:
                    self.data_collector.set_progress_callback(None)

    def _notify(self, message):
        try:
            notification_notify(title=_("番組情報の更新"), message=message, timeout=10)
        except Exception as e:
            self.log.error(f"Failed to send program cache notification: {e}")

    def _notify_collection_started(self, pending_count, total_count):
        """収集器が通信を始める直前に呼ばれる"""
        if self._retry_count:
            message = _("番組情報の更新を再試行しています（%(attempt)d回目、残り%(count)d局）。") % {
                "attempt": self._retry_count,
                "count": pending_count,
            }
        elif pending_count < total_count:
            message = _("前回の続きから番組情報の更新を再開しました（残り%(count)d局）。") % {"count": pending_count}
        else:
            message = _("番組情報の更新を開始しました（%(count)d局）。") % {"count": pending_count}
        self._notify(message)

    def _handle_collection_outcome(self):
        """バックグラウンド収集の結果を通知し、必要なら再試行を予約する"""
        outcome = self.data_collector.last_outcome
        if outcome == programDataCollector.OUTCOME_COMPLETED:
            self._notify(_("番組情報の更新が完了しました。"))
        if outcome in (programDataCollector.OUTCOME_COMPLETED, programDataCollector.OUTCOME_UP_TO_DATE):
            self._cancel_auto_retry()
            self._retry_count = 0
            return
        if outcome == programDataCollector.OUTCOME_ABORTED:
            message = _("radikoとの通信に失敗が続いたため、番組情報の更新を中断しました。")
        elif outcome == programDataCollector.OUTCOME_PARTIAL:
            message = _("番組情報の更新で、%(count)d局の取得に失敗しました。") % {
                "count": self.data_collector.last_failed_count,
            }
        else:
            # 停止要求や、始められなかった場合は知らせない
            return
        if self._retry_count < MAX_AUTO_RETRIES:
            self._schedule_auto_retry()
            message += _("%(minutes)d分後に再試行します。") % {"minutes": round(AUTO_RETRY_DELAY_SEC / 60)}
        else:
            self.log.warning(f"Program collection still failing after {MAX_AUTO_RETRIES} auto retries; giving up until next startup")
            self._retry_count = 0
            message += _("次回起動時に再試行します。")
        self._notify(message)

    def _schedule_auto_retry(self):
        self._cancel_auto_retry()
        self._retry_count += 1
        self.log.info(f"Scheduling program collection retry {self._retry_count}/{MAX_AUTO_RETRIES} in {AUTO_RETRY_DELAY_SEC}s")
        timer = threading.Timer(AUTO_RETRY_DELAY_SEC, self._run_auto_retry)
        timer.daemon = True
        self._retry_timer = timer
        timer.start()

    def _cancel_auto_retry(self):
        timer = self._retry_timer
        self._retry_timer = None
        if timer:
            timer.cancel()

    def _run_auto_retry(self):
        self._retry_timer = None
        if self._cleanup_done:
            return
        try:
            self.log.info(f"Retrying program collection ({self._retry_count}/{MAX_AUTO_RETRIES})")
            if self._collect_weekly_data() and self.cache_manager:
                self.cache_manager.cleanup_old_data(days=21)
        except Exception as e:
            self.log.error(f"Program collection retry failed: {e}")

    def _schedule_weekly_update_background(self):
        """週間データ更新をバックグラウンドで実行"""
        try:
            def background_weekly_update():
                try:
                    radio_date = CalendarUtil().get_radio_date()
                    self.log.info(f"Starting background weekly data collection from radio base date {radio_date}")
                    success = self._collect_weekly_data()
                    if success:
                        self.log.info("Background weekly database update completed successfully")
                        if self.cache_manager:
                            self.cache_manager.cleanup_old_data(days=21)
                    else:
                        self.log.warning("Background weekly database update failed")
                except Exception as e:
                    self.log.error(f"Background weekly update failed: {e}")

            threading.Thread(target=background_weekly_update, daemon=True).start()
        except Exception as e:
            self.log.error(f"Failed to schedule background weekly update: {e}")
    
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
            return self.force_weekly_update(force_refresh=False)
            
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
                    radio_date = CalendarUtil().get_radio_date()
                    self.log.info(f"Starting deferred weekly data collection from radio base date {radio_date}")
                    ok = self._collect_weekly_data()
                    if ok:
                        self.log.info("Deferred weekly database update completed successfully")
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
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self.log.info("ProgramCacheController.cleanup: starting (stop collection, close DB)")
        try:
            self._cancel_auto_retry()
            if self.data_collector:
                self.data_collector.cleanup()

            if self.cache_manager:
                self.log.info("ProgramCacheController: closing sqlite connection db=%s", self.db_path)
                self.cache_manager.close()
            else:
                self.log.info("ProgramCacheController: cache_manager not initialized; skipping DB")

            self.log.info("ProgramCacheController.cleanup: completed")

        except Exception as e:
            self.log.error("ProgramCacheController.cleanup: failed: %s", e, exc_info=True)
    
    def __del__(self):
        """デストラクタ"""
        self.cleanup()
