# -*- coding: utf-8 -*-
# 検索履歴管理モジュール

import os
import pickle
import datetime
from logging import getLogger
import constants

class SearchHistoryManager:
    """検索履歴を管理するクラス"""
    
    def __init__(self, history_file="search_history.dat"):
        self.history_file = history_file
        self.max_history = 20  # 最大履歴数
        self.log = getLogger(f"{constants.LOG_PREFIX}.SearchHistoryManager")
        
        # 履歴データの構造
        self.history = {
            'titles': [],      # 番組タイトル履歴
            'performers': []   # 出演者履歴
        }
        
        # 履歴ファイルから読み込み
        self.load_history()
    
    def load_history(self):
        """履歴ファイルから履歴を読み込む"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'rb') as f:
                    data = pickle.load(f)
                    if isinstance(data, dict) and 'titles' in data and 'performers' in data:
                        self.history = data
                        self.log.info(f"Loaded search history: {len(self.history['titles'])} titles, {len(self.history['performers'])} performers")
                    else:
                        self.log.warning("Invalid history file format, using empty history")
            else:
                self.log.info("No history file found, starting with empty history")
        except Exception as e:
            self.log.error(f"Failed to load search history: {e}")
            self.history = {'titles': [], 'performers': []}
    
    def save_history(self):
        """履歴をファイルに保存する"""
        try:
            with open(self.history_file, 'wb') as f:
                pickle.dump(self.history, f)
            self.log.debug("Search history saved successfully")
        except Exception as e:
            self.log.error(f"Failed to save search history: {e}")
    
    def add_title_history(self, title):
        """番組タイトル履歴に追加"""
        if not title or not title.strip():
            return
        
        title = title.strip()
        
        # 既存の同じタイトルを削除
        if title in self.history['titles']:
            self.history['titles'].remove(title)
        
        # 先頭に追加
        self.history['titles'].insert(0, title)
        
        # 最大件数を超えた場合は古いものを削除
        if len(self.history['titles']) > self.max_history:
            self.history['titles'] = self.history['titles'][:self.max_history]
        
        self.save_history()
        self.log.debug(f"Added title to history: '{title}'")
    
    def add_performer_history(self, performer):
        """出演者履歴に追加"""
        if not performer or not performer.strip():
            return
        
        performer = performer.strip()
        
        # 既存の同じ出演者を削除
        if performer in self.history['performers']:
            self.history['performers'].remove(performer)
        
        # 先頭に追加
        self.history['performers'].insert(0, performer)
        
        # 最大件数を超えた場合は古いものを削除
        if len(self.history['performers']) > self.max_history:
            self.history['performers'] = self.history['performers'][:self.max_history]
        
        self.save_history()
        self.log.debug(f"Added performer to history: '{performer}'")
    
    def get_title_history(self):
        """番組タイトル履歴を取得"""
        return self.history['titles'].copy()
    
    def get_performer_history(self):
        """出演者履歴を取得"""
        return self.history['performers'].copy()
    
    def clear_history(self):
        """履歴をクリア"""
        self.history = {'titles': [], 'performers': []}
        self.save_history()
        self.log.info("Search history cleared")
    
    def has_history(self):
        """履歴が存在するかチェック"""
        return len(self.history['titles']) > 0 or len(self.history['performers']) > 0
