"""TradingView Pine Script exporter for the AI momentum algorithm."""


def build_pine_script() -> str:
    return """//@version=6
strategy("AI Algorithm Bot - Momentum Edge", overlay=true, initial_capital=10000, commission_type=strategy.commission.percent, commission_value=0.02, pyramiding=0)

fastEmaLen = input.int(9, "Fast EMA", minval=1)
slowEmaLen = input.int(20, "Slow EMA", minval=1)
rsiLen = input.int(14, "RSI Length", minval=1)
rvLen = input.int(30, "Relative Volume Length", minval=5)
targetPct = input.float(2.0, "Target %", minval=0.1, step=0.1) / 100
stopPct = input.float(1.0, "Stop %", minval=0.1, step=0.1) / 100
minEdge = input.float(18.0, "Minimum Edge", minval=1.0)
minScore = input.float(58.0, "Minimum Score", minval=1.0)
webhookTag = input.string("ai-algo", "Webhook Tag")

emaFast = ta.ema(close, fastEmaLen)
emaSlow = ta.ema(close, slowEmaLen)
vwapValue = ta.vwap(hlc3)
rsiValue = ta.rsi(close, rsiLen)
relativeVolume = volume / math.max(ta.sma(volume, rvLen), 1)
recentMove = ((close - close[7]) / close[7]) * 100
higherLows = low >= low[2]
lowerHighs = high <= high[2]
recentHigh = ta.highest(high[1], 8)
recentLow = ta.lowest(low[1], 8)

float buyScore = 35
float sellScore = 35

if close > emaFast and emaFast > emaSlow
    buyScore += 16
else if close < emaFast and emaFast < emaSlow
    sellScore += 16
else if close > emaSlow
    buyScore += 7
else if close < emaSlow
    sellScore += 7

if close > vwapValue
    buyScore += 8
else if close < vwapValue
    sellScore += 8

if rsiValue >= 58
    buyScore += math.min(math.max((rsiValue - 50) * 0.65, 4), 16)
else if rsiValue <= 42
    sellScore += math.min(math.max((50 - rsiValue) * 0.65, 4), 16)

if recentMove >= 0.4
    buyScore += 10
else if recentMove <= -0.4
    sellScore += 10

if higherLows
    buyScore += 8
if lowerHighs
    sellScore += 8
if close > recentHigh
    buyScore += 12
if close < recentLow
    sellScore += 12

if relativeVolume >= 1.35
    if buyScore >= sellScore
        buyScore += 11
    else
        sellScore += 11
else if relativeVolume < 0.65
    buyScore -= 5
    sellScore -= 5

buyScore := math.min(math.max(buyScore, 0), 100)
sellScore := math.min(math.max(sellScore, 0), 100)
edge = buyScore - sellScore

longSignal = edge >= minEdge and buyScore >= minScore
shortSignal = edge <= -minEdge and sellScore >= minScore
longMessage = '{"source":"tradingview","action":"BUY","ticker":"' + syminfo.ticker + '","price":' + str.tostring(close) + ',"edge":' + str.tostring(edge) + ',"tag":"' + webhookTag + '"}'
shortMessage = '{"source":"tradingview","action":"SELL","ticker":"' + syminfo.ticker + '","price":' + str.tostring(close) + ',"edge":' + str.tostring(edge) + ',"tag":"' + webhookTag + '"}'

if longSignal and strategy.position_size <= 0
    strategy.entry("AI Long", strategy.long, alert_message=longMessage)
    alert(longMessage, alert.freq_once_per_bar_close)
if shortSignal and strategy.position_size >= 0
    strategy.entry("AI Short", strategy.short, alert_message=shortMessage)
    alert(shortMessage, alert.freq_once_per_bar_close)

longStop = strategy.position_avg_price * (1 - stopPct)
longTarget = strategy.position_avg_price * (1 + targetPct)
shortStop = strategy.position_avg_price * (1 + stopPct)
shortTarget = strategy.position_avg_price * (1 - targetPct)

strategy.exit("AI Long Exit", "AI Long", stop=longStop, limit=longTarget)
strategy.exit("AI Short Exit", "AI Short", stop=shortStop, limit=shortTarget)

plot(emaFast, "EMA 9", color=color.new(color.teal, 0))
plot(emaSlow, "EMA 20", color=color.new(color.orange, 0))
plot(vwapValue, "VWAP", color=color.new(color.blue, 0))
plotchar(longSignal, "BUY", "B", location.belowbar, color=color.lime)
plotchar(shortSignal, "SELL", "S", location.abovebar, color=color.red)
"""


if __name__ == "__main__":
    print(build_pine_script())
