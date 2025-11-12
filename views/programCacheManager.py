# -*- coding: utf-8 -*-
# 番組表キャッシュ管理モジュール

import sqlite3
import os
import datetime
import threading
from logging import getLogger
import constants
import globalVars
from views import programmanager

class ProgramCacheManager:
    """番組表データのSQLite3キャッシュ管理クラス"""
    
    def __init__(self, db_path=None):
        self.log = getLogger(f"{constants.LOG_PREFIX}.ProgramCacheManager")
        self.db_path = db_path or constants.PROGRAM_CACHE_DB_NAME
        self.lock = threading.Lock()
        self.conn = None
        self._init_database()
    
    def _init_database(self):
        """データベースの初期化"""
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row  # 辞書形式でアクセス可能に
            self._create_tables()
            self._create_indexes()
            self.log.info(f"Database initialized: {self.db_path}")
        except sqlite3.Error as e:
            self.log.error(f"Database initialization failed: {e}")
            raise
    
    def _create_tables(self):
        """テーブル作成"""
        cursor = self.conn.cursor()
        
        # 番組データテーブル
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id TEXT NOT NULL,
                station_name TEXT NOT NULL,
                title TEXT NOT NULL,
                performer TEXT,
                start_time DATETIME NOT NULL,
                end_time DATETIME NOT NULL,
                description TEXT,
                date TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # キャッシュメタデータテーブル
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cache_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
    
    def _create_indexes(self):
        """検索用インデックス作成"""
        cursor = self.conn.cursor()
        
        # 検索用インデックス
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_title ON programs(title)",
            "CREATE INDEX IF NOT EXISTS idx_performer ON programs(performer)",
            "CREATE INDEX IF NOT EXISTS idx_time_range ON programs(start_time, end_time)",
            "CREATE INDEX IF NOT EXISTS idx_station_date ON programs(station_id, date)",
            "CREATE INDEX IF NOT EXISTS idx_station_name ON programs(station_name)",
            "CREATE INDEX IF NOT EXISTS idx_fulltext ON programs(title, performer, description)"
        ]
        
        for index_sql in indexes:
            cursor.execute(index_sql)
        
        self.conn.commit()
    
    def update_programs_data(self, programs_data, date):
        """番組データの一括更新"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 既存データを削除（指定日付のデータのみ）
                cursor.execute("DELETE FROM programs WHERE date = ?", (date,))
                
                # 新しいデータを挿入
                insert_data = []
                for station_id, station_data in programs_data.items():
                    station_name = station_data.get('name', '')
                    for program in station_data.get('programs', []):
                        insert_data.append((
                            station_id,
                            station_name,
                            program.get('title', ''),
                            program.get('performer', ''),
                            program.get('start_time', ''),
                            program.get('end_time', ''),
                            program.get('description', ''),
                            date
                        ))
                
                cursor.executemany('''
                    INSERT INTO programs 
                    (station_id, station_name, title, performer, start_time, end_time, description, date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', insert_data)
                
                # メタデータを更新
                cursor.execute('''
                    INSERT OR REPLACE INTO cache_metadata (key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                ''', ('last_update', datetime.datetime.now().strftime('%Y%m%d')))
                
                self.conn.commit()
                self.log.info(f"Updated {len(insert_data)} programs for date {date}")
                
            except sqlite3.Error as e:
                self.log.error(f"Failed to update programs data: {e}")
                self.conn.rollback()
                raise
    
    def search_programs(self, search_criteria):
        """番組検索実行"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 検索条件の構築
                where_conditions = []
                params = []
                
                if search_criteria.get('title'):
                    where_conditions.append("title LIKE ?")
                    params.append(f"%{search_criteria['title']}%")
                
                if search_criteria.get('performer'):
                    where_conditions.append("performer LIKE ?")
                    params.append(f"%{search_criteria['performer']}%")
                
                if search_criteria.get('start_time'):
                    where_conditions.append("start_time >= ?")
                    params.append(search_criteria['start_time'])
                
                if search_criteria.get('end_time'):
                    where_conditions.append("end_time <= ?")
                    params.append(search_criteria['end_time'])
                
                if search_criteria.get('station_name'):
                    where_conditions.append("station_name LIKE ?")
                    params.append(f"%{search_criteria['station_name']}%")
                
                if search_criteria.get('date'):
                    where_conditions.append("date = ?")
                    params.append(search_criteria['date'])
                else:
                    # 日付が指定されていない場合、現在の日付以降の番組のみを検索
                    # ラジオの日付ルールに従った日付を取得
                    from tcutil import CalendarUtil
                    calendar_util = CalendarUtil()
                    today = calendar_util.get_radio_date()
                    where_conditions.append("date >= ?")
                    params.append(today)
                
                # 検索クエリの構築
                where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
                limit = search_criteria.get('limit', 100)
                
                query = f'''
                    SELECT station_id, station_name, title, performer, 
                           start_time, end_time, description, date
                    FROM programs 
                    WHERE {where_clause}
                    ORDER BY date, start_time
                    LIMIT ?
                '''
                params.append(limit)
                
                cursor.execute(query, params)
                results = cursor.fetchall()
                
                # 辞書形式に変換
                programs = []
                for row in results:
                    programs.append({
                        'station_id': row['station_id'],
                        'station_name': row['station_name'],
                        'title': row['title'],
                        'performer': row['performer'],
                        'start_time': row['start_time'],
                        'end_time': row['end_time'],
                        'description': row['description'],
                        'date': row['date']
                    })
                
                # 過去の番組を除外（日付と時間を組み合わせて現在時刻と比較）
                programs = self._filter_past_programs(programs)
                
                self.log.info(f"Search completed: {len(programs)} results found")
                return programs
                
            except sqlite3.Error as e:
                self.log.error(f"Search failed: {e}")
                return []
    
    def _filter_past_programs(self, programs):
        """過去の番組を除外"""
        import datetime
        
        if not programs:
            return programs
        
        current = datetime.datetime.now()
        
        filtered_programs = []
        for program in programs:
            try:
                date_str = program.get('date', '')
                start_time_str = program.get('start_time', '')
                
                if not date_str or not start_time_str:
                    # 日付または時間が不明な場合は除外
                    continue
                
                # 日付をパース
                if len(date_str) == 8:
                    year = int(date_str[:4])
                    month = int(date_str[4:6])
                    day = int(date_str[6:8])
                    program_date = datetime.date(year, month, day)
                else:
                    continue
                
                # 時間をパース（HH:MM:SS形式またはHH:MM形式）
                start_parts = start_time_str.split(':')
                if len(start_parts) >= 2:
                    start_hour = int(start_parts[0])
                    start_minute = int(start_parts[1])
                else:
                    continue
                
                # datetimeオブジェクトを作成
                program_datetime = datetime.datetime.combine(
                    program_date,
                    datetime.time(start_hour, start_minute)
                )
                
                # 深夜番組の処理（開始時間が4:59以前の場合は翌日として扱う）
                if program_datetime.time() < datetime.time(4, 59, 59):
                    program_datetime += datetime.timedelta(days=1)
                
                # 現在時刻より後の番組のみを含める
                if program_datetime >= current:
                    filtered_programs.append(program)
                    
            except (ValueError, TypeError) as e:
                # パースエラーの場合は除外
                self.log.debug(f"Failed to parse program date/time, excluding: {e}")
                continue
        
        return filtered_programs
    
    def get_program_count(self, date=None):
        """キャッシュされた番組数を取得"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if date:
                    cursor.execute("SELECT COUNT(*) FROM programs WHERE date = ?", (date,))
                else:
                    cursor.execute("SELECT COUNT(*) FROM programs")
                return cursor.fetchone()[0]
            except sqlite3.Error as e:
                self.log.error(f"Failed to get program count: {e}")
                return 0
    
    def get_last_update_time(self):
        """最終更新時刻を取得"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("SELECT value FROM cache_metadata WHERE key = 'last_update'")
                result = cursor.fetchone()
                return result[0] if result else None
            except sqlite3.Error as e:
                self.log.error(f"Failed to get last update time: {e}")
                return None
    
    def cleanup_old_data(self, days=7):
        """古いデータの削除"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y%m%d')
                cursor.execute("DELETE FROM programs WHERE date < ?", (cutoff_date,))
                deleted_count = cursor.rowcount
                self.conn.commit()
                self.log.info(f"Cleaned up {deleted_count} old program records")
                return deleted_count
            except sqlite3.Error as e:
                self.log.error(f"Failed to cleanup old data: {e}")
                return 0
    
    def is_cache_valid(self, date, max_age_hours=1):
        """キャッシュの有効性をチェック"""
        try:
            last_update = self.get_last_update_time()
            if not last_update:
                self.log.debug("No last update time found, cache invalid")
                return False
            
            # 日付の形式をチェック
            if len(last_update) < 8:
                self.log.warning(f"Invalid last update format: {last_update}")
                return False
            
            # 指定された日付のデータが存在するかチェック
            program_count = self.get_program_count(date)
            if program_count == 0:
                self.log.debug(f"No programs found for date {date}, cache invalid")
                return False
            
            last_update_time = datetime.datetime.fromisoformat(last_update)
            age = datetime.datetime.now() - last_update_time
            is_valid = age.total_seconds() < (max_age_hours * 3600)
            
            self.log.debug(f"Cache validity check: last_update={last_update}, age={age.total_seconds()/3600:.1f}h, valid={is_valid}")
            return is_valid
            
        except Exception as e:
            self.log.error(f"Failed to check cache validity: {e}")
            return False
    
    def get_weekly_data_summary(self):
        """1週間分のデータサマリーを取得"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                
                # 今日から1週間分の日付範囲を計算
                today = datetime.datetime.now()
                week_dates = []
                for i in range(7):
                    target_date = today + datetime.timedelta(days=i)
                    week_dates.append(target_date.strftime('%Y%m%d'))
                
                # 各日付のデータ数を取得
                summary = {}
                for date_str in week_dates:
                    cursor.execute("SELECT COUNT(*) FROM programs WHERE date = ?", (date_str,))
                    count = cursor.fetchone()[0]
                    summary[date_str] = count
                
                # 総データ数
                cursor.execute("SELECT COUNT(*) FROM programs")
                total_count = cursor.fetchone()[0]
                
                # 放送局数
                cursor.execute("SELECT COUNT(DISTINCT station_id) FROM programs")
                station_count = cursor.fetchone()[0]
                
                return {
                    'weekly_summary': summary,
                    'total_programs': total_count,
                    'total_stations': station_count,
                    'date_range': week_dates
                }
                
            except sqlite3.Error as e:
                self.log.error(f"Failed to get weekly data summary: {e}")
                return None
    
    def is_weekly_cache_complete(self):
        """1週間分のキャッシュが完全かチェック"""
        try:
            summary = self.get_weekly_data_summary()
            if not summary:
                return False
            
            # 各日付に最低限のデータがあるかチェック
            min_programs_per_day = 10  # 最低限の番組数
            for date_str, count in summary['weekly_summary'].items():
                if count < min_programs_per_day:
                    self.log.debug(f"Insufficient data for {date_str}: {count} programs")
                    return False
            
            self.log.info(f"Weekly cache is complete: {summary['total_programs']} programs across {summary['total_stations']} stations")
            return True
            
        except Exception as e:
            self.log.error(f"Failed to check weekly cache completeness: {e}")
            return False
    
    def get_available_date_range(self):
        """利用可能な日付範囲を取得"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("SELECT MIN(date), MAX(date) FROM programs")
                result = cursor.fetchone()
                
                if result and result[0] and result[1]:
                    return {
                        'start_date': result[0],
                        'end_date': result[1],
                        'days_available': (datetime.datetime.strptime(result[1], '%Y%m%d') - 
                                         datetime.datetime.strptime(result[0], '%Y%m%d')).days + 1
                    }
                else:
                    return None
                    
            except sqlite3.Error as e:
                self.log.error(f"Failed to get available date range: {e}")
                return None
    
    def close(self):
        """データベース接続を閉じる"""
        if self.conn:
            self.conn.close()
            self.log.info("Database connection closed")
    
    def __del__(self):
        """デストラクタ"""
        self.close()
