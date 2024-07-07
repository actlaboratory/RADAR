#rpb time&calenderUtil(tcutil)

import ConfigManager
import calendar
import datetime

class TimeManager:
    def __init__(self):
        """コンストラクタ"""
        self.config = ConfigManager.ConfigManager()

    def replace_milliseconds(self, minutes):
        """
        引数の分数をミリ秒に変換して返す関数

        Args:
            minutes (int): 変換したい分数
        
        Returns:
            int: 分数をミリ秒に変換した値
        """
        milliseconds = minutes * 60 * 1000
        return int(milliseconds)

    def calculate_time_difference(self, current_time, program_start_time):
        """
        現在時刻と番組開始時間の差をミリ秒で返す関数
        Args:
            current_time (datetime.datetime): 現在の日時
            program_start_time (datetime.datetime): 番組の開始時間
        Returns:
            int: 現在時刻と番組開始時間の差をミリ秒で表した値
        """
        time_difference = program_start_time - current_time
        return int(time_difference.total_seconds() * 1000)


class CalendarUtil:
    def __init__(self):
        """カレンダークラス"""
        self.year = datetime.datetime.now().year
        if len(str(datetime.datetime.now().month)) < 2:
            self.month = f"0{datetime.datetime.now().month}"
        else:
            self.month = f"{datetime.datetime.now().month}"
        print(type(self.month))

    def getAnnual(self):
        """年間カレンダー取得"""
        return calendar.prcal(self.year)

    def getMonth(self):
        """月間カレンダー取得"""
        month = datetime.datetime.now().month
        return calendar.monthcalendar(self.year, month)

    def dateToInteger(self, date):
        """日付データから/を除去し、int型に変換して返す"""

        if "/" in date:
            result = date.replace("/", "")
            return result
        if "-" in date:
            result = date.replace("-", "")
            return result