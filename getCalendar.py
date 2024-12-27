import datetime

class Calendar:
    def generate_calendar(self, year):
        calendar = []
        start_date = datetime.date(year, 1, 1)
        delta = datetime.timedelta(days=1)
        end_date = datetime.date(year, 12, 31)

        current_date = start_date
        while current_date <= end_date:
            calendar.append(current_date)
            current_date += delta
        return calendar

    def get_week_dates(self, start_date, days=7):
        week_dates = []
        current_date = start_date
        delta = datetime.timedelta(days=1)

        for _ in range(days):
            week_dates.append((current_date.year, current_date.month, current_date.day))
            current_date += delta
        return week_dates

    def adjust_date(self, year, month):
        if month > 12:
            year += 1
            month = 1
        return year, month


