import os
from tvDatafeed import TvDatafeed, Interval

OUTPUT_DIR = "data/TV/equities/SYMBOL/5min"

os.makedirs(OUTPUT_DIR, exist_ok=True)

tv = TvDatafeed()
qqq_tv_data = tv.get_hist(
    symbol='SYMBOL',
    exchange='NASDAQ',
    interval=Interval.in_5_minute,
    n_bars=5500
)

first_date = qqq_tv_data.index[0].strftime("%Y-%m-%d")
last_date  = qqq_tv_data.index[-1].strftime("%Y-%m-%d")
filename   = f"{first_date}-TO-{last_date}.csv"

save_location = os.path.join(OUTPUT_DIR, filename)
qqq_tv_data.to_csv(save_location)

print(f"Data successfully saved to {save_location}")
