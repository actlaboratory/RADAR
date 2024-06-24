import ConfigManager

class TimeManager:
    def __init__(self):
        """コンストラクタ"""
        self.config = ConfigManager.ConfigManager()

    def minutes_to_milliseconds(self, minutes):
        """
        引数の分数をミリ秒に変換して返す関数

        Args:
            minutes (int): 変換したい分数
        
        Returns:
            int: 分数をミリ秒に変換した値
        """
        milliseconds = minutes * 60 * 1000
        return int(milliseconds)
