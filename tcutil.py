#rpb time&calenderUtil
import ConfigManager
import calendar

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

    def calculate_time_difference(current_time, program_start_time):
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


class CalenderUtil:
    def __init__(self):
        """カレンダークラス"""

    def getAnnual(self):
        """年間カレンダー取得"""
        self.year = int(datetime.datetime.now().year)
        return calendar.prcal(year)

    def getMonth(self):
        """月間カレンダー取得"""
        month = int(datetime.datetime.now().month)
        return calendar.month(self.year, month)

    def judge_leapYear(self):
        """閏年かどうかを判定"""
        return calendar.isleap(self.year)