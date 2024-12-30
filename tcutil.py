#rpb time&calenderUtil
import ConfigManager
import getCalendar
import datetime

class TimeManager:
    def __init__(self):
        """時間を管理する"""
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
        """カレンダーを扱う"""
        self.dateData = getCalendar.Calendar()

    def getDateValue(self):
        """日付データを取得"""
        current = datetime.datetime.now()
        year = current.year
        calendar = self.dateData.generate_calendar(year)
        start_date = datetime.date(year, current.month, current.day)
        week_data = self.dateData.get_week_dates(start_date)
        results = []
        for date in week_data:
            year, month, day = date
            # 月末・年末処理
            if day > 31:
                month += 1
                year, month = self.dateData.adjust_date(year, month)
            formatted_data = f"{year}/{month}/{day}"
            results.append(formatted_data)
        return results

    def dateToInteger(self, date):
        """日付データから/を除去し、int型に変換して返す"""
        if "/" in date:
            result = date.replace("/", "")
            return result
        if "-" in date:
            result = date.replace("-", "")
            return result

    def format_now(self):
        now = datetime.datetime.now()

        # 年・月・日・時・分・秒を取得
        year = now.year
        month = now.month
        day = now.day
        hour = now.hour
        minute = now.minute

        # 結果を出力
        return int(f"{year}{month}{day}{hour}{minute}")