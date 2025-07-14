from flask import Flask, render_template, request
import pandas as pd
from datetime import datetime
import os

app = Flask(__name__)

# Load instruments file
instruments_df = pd.read_csv("instruments.csv")

@app.route("/", methods=["GET", "POST"])
def index():
    signal_data = []
    volume_breakout_pct = 25  # default threshold
    from_date = ""
    to_date = ""

    if request.method == "POST":
        instrument = request.form.get("instrument")
        volume_breakout_pct = float(request.form.get("volume_breakout") or 25)
        from_date = request.form.get("from_date")
        to_date = request.form.get("to_date")

        # Load stock data (replace with your source e.g., Kite API or CSV)
        df = pd.read_csv(f"data/{instrument}.csv")
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        # Filter by date range
        if from_date and to_date:
            df = df[(df["date"] >= from_date) & (df["date"] <= to_date)]

        for i in range(2, len(df)):
            current_close = df.loc[i, "close"]
            prev_close = df.loc[i - 1, "close"]
            current_volume = df.loc[i, "volume"]
            prev1_volume = df.loc[i - 1, "volume"]
            prev2_volume = df.loc[i - 2, "volume"]

            volume_change_pct = ((current_volume - prev1_volume) / prev1_volume) * 100 if prev1_volume != 0 else 0

            signal = ""
            if (
                current_close > prev_close and
                volume_change_pct > volume_breakout_pct and
                prev1_volume < 0 and prev2_volume < 0
            ):
                signal = "BULLISH"
            elif (
                current_close < prev_close and
                volume_change_pct > volume_breakout_pct and
                prev1_volume < 0 and prev2_volume < 0
            ):
                signal = "BEARISH"

            if signal:
                signal_data.append({
                    "date": df.loc[i, "date"],
                    "open": df.loc[i, "open"],
                    "high": df.loc[i, "high"],
                    "low": df.loc[i, "low"],
                    "close": current_close,
                    "volume": current_volume,
                    "signal": signal
                })

    instruments = instruments_df["tradingsymbol"].unique().tolist()

    return render_template("vpa.html", signals=signal_data, instruments=instruments,
                           selected_volume=volume_breakout_pct, from_date=from_date, to_date=to_date)

if __name__ == "__main__":
    app.run(debug=True)
