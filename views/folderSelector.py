# -*- coding: utf-8 -*-
# フォルダ選択ダイアログ用ヘルパークラス
# Copyright (C) 2021 yamahubuki <itiro.ishino@gmail.com>

import wx
import os
import simpleDialog
from logging import getLogger

class FolderSelector:
    """フォルダ選択ダイアログのヘルパークラス"""
    
    def __init__(self, parent, title=_("フォルダを選択")):
        self.parent = parent
        self.title = title
        self.log = getLogger("FolderSelector")
    
    def select_folder(self, default_path=""):
        """
        フォルダ選択ダイアログを表示し、選択されたフォルダのパスを返す
        
        Args:
            default_path (str): デフォルトで選択されるパス
            
        Returns:
            str: 選択されたフォルダのパス。キャンセルされた場合は空文字列
        """
        try:
            # デフォルトパスが指定されていない場合は、現在のディレクトリを使用
            if not default_path:
                default_path = os.getcwd()
            
            # パスが存在しない場合は、親ディレクトリを探す
            if not os.path.exists(default_path):
                default_path = os.path.dirname(default_path)
                if not os.path.exists(default_path):
                    default_path = os.getcwd()
            
            # フォルダ選択ダイアログを表示
            dialog = wx.DirDialog(
                self.parent,
                self.title,
                defaultPath=default_path,
                style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST
            )
            
            result = dialog.ShowModal()
            selected_path = ""
            
            if result == wx.ID_OK:
                selected_path = dialog.GetPath()
                self.log.info(f"Selected folder: {selected_path}")
            
            dialog.Destroy()
            return selected_path
            
        except Exception as e:
            self.log.error(f"Error in folder selection: {e}")
            simpleDialog.errorDialog(f"フォルダ選択中にエラーが発生しました: {str(e)}")
            return ""
    
    def validate_folder_path(self, folder_path):
        """
        フォルダパスの妥当性をチェックする
        
        Args:
            folder_path (str): チェックするフォルダパス
            
        Returns:
            tuple: (is_valid, error_message)
        """
        if not folder_path:
            return False, "フォルダパスが指定されていません"
        
        # パスが存在するかチェック
        if not os.path.exists(folder_path):
            return False, "指定されたフォルダが存在しません"
        
        # ディレクトリかどうかチェック
        if not os.path.isdir(folder_path):
            return False, "指定されたパスはフォルダではありません"
        
        # 書き込み権限があるかチェック
        try:
            test_file = os.path.join(folder_path, "test_write_permission.tmp")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except (PermissionError, OSError) as e:
            return False, f"フォルダへの書き込み権限がありません: {str(e)}"
        
        # システムディレクトリかどうかチェック（Windows）
        if os.name == 'nt':
            system_dirs = [
                os.path.expandvars("%SystemRoot%"),
                os.path.expandvars("%ProgramFiles%"),
                os.path.expandvars("%ProgramFiles(x86)%"),
                os.path.expandvars("%SystemDrive%\\Windows"),
            ]
            
            for sys_dir in system_dirs:
                if folder_path.lower().startswith(sys_dir.lower()):
                    return False, "システムディレクトリは選択できません"
        
        return True, ""
    
    def create_folder_if_not_exists(self, folder_path):
        """
        フォルダが存在しない場合は作成する
        
        Args:
            folder_path (str): 作成するフォルダパス
            
        Returns:
            tuple: (success, error_message)
        """
        try:
            if not os.path.exists(folder_path):
                os.makedirs(folder_path, exist_ok=True)
                self.log.info(f"Created folder: {folder_path}")
            return True, ""
        except Exception as e:
            error_msg = f"フォルダの作成に失敗しました: {str(e)}"
            self.log.error(error_msg)
            return False, error_msg
