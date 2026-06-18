import pathlib
import sys

# RSI/ is a top-level dir (not a package); make rsi_paper / rsi_backtest importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "RSI"))
