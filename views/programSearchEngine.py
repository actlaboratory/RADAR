# -*- coding: utf-8 -*-
# 番組検索エンジンモジュール

import datetime
import re
from logging import getLogger
import constants
from views.programCacheManager import ProgramCacheManager

class ProgramSearchEngine:
    """番組検索エンジン"""
    
    def __init__(self, cache_manager=None):
        self.log = getLogger(f"{constants.LOG_PREFIX}.ProgramSearchEngine")
        self.cache_manager = cache_manager or ProgramCacheManager()
    
    def search_by_title(self, query, limit=100, date=None):
        """番組タイトルで検索"""
        return self._search_with_criteria({'title': query, 'limit': limit, 'date': date})
    
    def search_by_performer(self, query, limit=100, date=None):
        """出演者で検索"""
        return self._search_with_criteria({'performer': query, 'limit': limit, 'date': date})
    
    def search_by_time_range(self, start_time, end_time, limit=100, date=None):
        """時間範囲で検索"""
        return self._search_with_criteria({
            'start_time': start_time, 
            'end_time': end_time, 
            'limit': limit, 
            'date': date
        })
    
    def search_by_station(self, station_name, limit=100, date=None):
        """放送局で検索"""
        return self._search_with_criteria({'station_name': station_name, 'limit': limit, 'date': date})
    
    def _search_with_criteria(self, criteria):
        """検索条件で検索を実行"""
        # Noneの値を除去
        search_criteria = {k: v for k, v in criteria.items() if v is not None}
        return self.cache_manager.search_programs(search_criteria)
    
    def search_combined(self, title=None, performer=None, station_name=None, 
                       start_time=None, end_time=None, date=None, limit=100, 
                       use_time_range_search=False):
        """複合検索"""
        search_criteria = {
            'limit': limit
        }
        
        if title:
            search_criteria['title'] = title
        if performer:
            search_criteria['performer'] = performer
        if station_name:
            search_criteria['station_name'] = station_name
        if start_time:
            search_criteria['start_time'] = start_time
        if end_time:
            search_criteria['end_time'] = end_time
        if date:
            search_criteria['date'] = date
        
        # 時間範囲検索の場合は特別な処理
        if use_time_range_search and start_time and end_time:
            return self._search_by_time_range_with_overlap(search_criteria)
        
        return self.cache_manager.search_programs(search_criteria)
    
    def _search_by_time_range_with_overlap(self, search_criteria):
        """時間範囲で重複する番組を検索（例：7:00-9:30で6:00-9:00の番組も含む）"""
        try:
            cursor = self.cache_manager.conn.cursor()
            
            # 基本検索条件を構築
            where_conditions = []
            params = []
            
            if search_criteria.get('title'):
                where_conditions.append("title LIKE ?")
                params.append(f"%{search_criteria['title']}%")
            
            if search_criteria.get('performer'):
                where_conditions.append("performer LIKE ?")
                params.append(f"%{search_criteria['performer']}%")
            
            if search_criteria.get('station_name'):
                where_conditions.append("station_name LIKE ?")
                params.append(f"%{search_criteria['station_name']}%")
            
            if search_criteria.get('date'):
                where_conditions.append("date = ?")
                params.append(search_criteria['date'])
            
            # 時間範囲の重複検索ロジック
            start_time = search_criteria.get('start_time')
            end_time = search_criteria.get('end_time')
            
            if start_time and end_time:
                # 番組の開始時間が検索終了時間より前 かつ
                # 番組の終了時間が検索開始時間より後
                # つまり、時間的に重複する番組を検索
                where_conditions.append("start_time < ? AND end_time > ?")
                params.extend([end_time, start_time])
            
            # クエリの構築
            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
            limit = search_criteria.get('limit', 100)
            
            query = f'''
                SELECT station_id, station_name, title, performer, 
                       start_time, end_time, description, date
                FROM programs 
                WHERE {where_clause}
                ORDER BY start_time
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
            
            self.log.info(f"Time range search completed: {len(programs)} results found")
            return programs
            
        except Exception as e:
            self.log.error(f"Time range search failed: {e}")
            return []
    
    def search_now_playing(self, station_id=None):
        """現在放送中の番組を検索"""
        now = datetime.datetime.now()
        current_time = now.strftime('%H:%M:%S')
        
        # ラジオの日付ルールに従った日付を取得
        from tcutil import CalendarUtil
        calendar_util = CalendarUtil()
        today = calendar_util.get_radio_date()
        
        search_criteria = {
            'start_time': f"00:00:00",
            'end_time': current_time,
            'date': today,
            'limit': 1
        }
        
        if station_id:
            # 特定の放送局の現在放送中番組を検索
            programs = self.cache_manager.search_programs(search_criteria)
            for program in programs:
                if program['station_id'] == station_id:
                    return program
            return None
        else:
            # 全放送局の現在放送中番組を検索
            return self.cache_manager.search_programs(search_criteria)
    
    def search_upcoming_programs(self, hours=24, station_id=None):
        """今後の番組を検索"""
        now = datetime.datetime.now()
        current_time = now.strftime('%H:%M:%S')
        future_time = (now + datetime.timedelta(hours=hours)).strftime('%H:%M:%S')
        
        # ラジオの日付ルールに従った日付を取得
        from tcutil import CalendarUtil
        calendar_util = CalendarUtil()
        today = calendar_util.get_radio_date()
        
        search_criteria = {
            'start_time': current_time,
            'end_time': future_time,
            'date': today,
            'limit': 50
        }
        
        if station_id:
            search_criteria['station_id'] = station_id
        
        return self.cache_manager.search_programs(search_criteria)
    
    def search_by_keywords(self, keywords, search_fields=['title', 'performer', 'description'], limit=100):
        """キーワード検索（複数フィールド対象）"""
        results = []
        
        for field in search_fields:
            if field == 'title':
                field_results = self.search_by_title(keywords, limit)
            elif field == 'performer':
                field_results = self.search_by_performer(keywords, limit)
            elif field == 'description':
                # 説明文での検索は複合検索で実装
                search_criteria = {
                    'description': keywords,
                    'limit': limit
                }
                field_results = self.cache_manager.search_programs(search_criteria)
            else:
                continue
            
            results.extend(field_results)
        
        # 重複を除去
        seen = set()
        unique_results = []
        for result in results:
            result_key = (result['station_id'], result['title'], result['start_time'])
            if result_key not in seen:
                seen.add(result_key)
                unique_results.append(result)
        
        return unique_results[:limit]
    
    def get_popular_programs(self, date=None, limit=20):
        """人気番組を取得（タイトルが重複する番組を集計）"""
        try:
            cursor = self.cache_manager.conn.cursor()
            
            where_clause = "WHERE date = ?" if date else "WHERE 1=1"
            params = [date] if date else []
            
            query = f'''
                SELECT title, station_name, COUNT(*) as count,
                       MIN(start_time) as first_start_time,
                       MAX(end_time) as last_end_time
                FROM programs 
                {where_clause}
                GROUP BY title, station_name
                ORDER BY count DESC, title
                LIMIT ?
            '''
            params.append(limit)
            
            cursor.execute(query, params)
            results = cursor.fetchall()
            
            popular_programs = []
            for row in results:
                popular_programs.append({
                    'title': row['title'],
                    'station_name': row['station_name'],
                    'count': row['count'],
                    'first_start_time': row['first_start_time'],
                    'last_end_time': row['last_end_time']
                })
            
            return popular_programs
            
        except Exception as e:
            self.log.error(f"Failed to get popular programs: {e}")
            return []
    
    def get_station_schedule(self, station_id, date=None):
        """特定放送局の番組表を取得"""
        if date is None:
            date = datetime.datetime.now().strftime('%Y%m%d')
        
        search_criteria = {
            'station_id': station_id,
            'date': date,
            'limit': 1000  # 1日の番組数上限
        }
        
        return self.cache_manager.search_programs(search_criteria)
    
    def search_similar_programs(self, program_title, limit=10):
        """類似番組を検索（タイトルが似ている番組）"""
        try:
            cursor = self.cache_manager.conn.cursor()
            
            # タイトルからキーワードを抽出
            keywords = self._extract_keywords(program_title)
            if not keywords:
                return []
            
            # キーワードを含む番組を検索
            where_conditions = []
            params = []
            
            for keyword in keywords:
                where_conditions.append("title LIKE ?")
                params.append(f"%{keyword}%")
            
            where_clause = " OR ".join(where_conditions)
            
            query = f'''
                SELECT DISTINCT station_id, station_name, title, performer, 
                       start_time, end_time, description, date
                FROM programs 
                WHERE {where_clause}
                AND title != ?
                ORDER BY start_time DESC
                LIMIT ?
            '''
            params.extend([program_title, limit])
            
            cursor.execute(query, params)
            results = cursor.fetchall()
            
            similar_programs = []
            for row in results:
                similar_programs.append({
                    'station_id': row['station_id'],
                    'station_name': row['station_name'],
                    'title': row['title'],
                    'performer': row['performer'],
                    'start_time': row['start_time'],
                    'end_time': row['end_time'],
                    'description': row['description'],
                    'date': row['date']
                })
            
            return similar_programs
            
        except Exception as e:
            self.log.error(f"Failed to search similar programs: {e}")
            return []
    
    def _extract_keywords(self, text):
        """テキストからキーワードを抽出"""
        if not text:
            return []
        
        # ひらがな、カタカナ、漢字、英数字以外を除去
        cleaned_text = re.sub(r'[^\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF\u0030-\u0039\u0041-\u005A\u0061-\u007A]', ' ', text)
        
        # 単語に分割（2文字以上のもののみ）
        words = [word.strip() for word in cleaned_text.split() if len(word.strip()) >= 2]
        
        return words[:5]  # 最大5個のキーワード
    
    def get_search_suggestions(self, partial_query, limit=10):
        """検索候補を取得"""
        try:
            cursor = self.cache_manager.conn.cursor()
            
            query = '''
                SELECT DISTINCT title
                FROM programs 
                WHERE title LIKE ?
                ORDER BY title
                LIMIT ?
            '''
            
            cursor.execute(query, [f"%{partial_query}%", limit])
            results = cursor.fetchall()
            
            return [row['title'] for row in results]
            
        except Exception as e:
            self.log.error(f"Failed to get search suggestions: {e}")
            return []
