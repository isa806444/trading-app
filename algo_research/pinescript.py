"""TradingView Pine Script exporter for the NQ futures paper strategy."""


def build_pine_script() -> str:
    return """//@version=6
//@strategy_alert_message {{strategy.order.alert_message}}
strategy("AI NQ 1H Paper Bot - Trend Breakout", overlay=true, initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=1, commission_type=strategy.commission.cash_per_contract, commission_value=2.5, slippage=1, pyramiding=0, process_orders_on_close=true, calc_on_every_tick=false)

contractQty = input.int(1, "Contracts", minval=1, maxval=10)
requireOneHour = input.bool(true, "Only Fire On 1H Chart")
emaTrendLen = input.int(200, "Trend EMA", minval=20)
emaSignalLen = input.int(34, "Signal EMA", minval=5)
rsiLen = input.int(14, "RSI Length", minval=2)
adxLen = input.int(14, "ADX Length", minval=2)
adxSmoothing = input.int(14, "ADX Smoothing", minval=2)
breakoutLen = input.int(8, "Breakout Lookback", minval=2)
atrLen = input.int(14, "ATR Stop Length", minval=2)
atrStopMult = input.float(1.6, "ATR Stop Multiplier", minval=0.25, step=0.05)
rewardToRisk = input.float(2.0, "Reward/Risk Target", minval=0.5, step=0.1)
minAdx = input.float(18.0, "Minimum Trend Strength", minval=1.0, step=0.5)
minScore = input.float(75.0, "Minimum Setup Score", minval=50.0, maxval=100.0, step=1.0)
useSessionFilter = input.bool(false, "Use Regular Session Filter")
tradeSession = input.session("0930-1600", "Trading Session")
webhookTag = input.string("nq-1h-paper", "Webhook Tag")

timeframeOk = not requireOneHour or (timeframe.isminutes and timeframe.multiplier == 60)
inSession = not useSessionFilter or not na(time(timeframe.period, tradeSession))
emaTrend = ta.ema(close, emaTrendLen)
emaSignal = ta.ema(close, emaSignalLen)
rsiValue = ta.rsi(close, rsiLen)
[plusDi, minusDi, adxValue] = ta.dmi(adxLen, adxSmoothing)
atrValue = ta.atr(atrLen)
priorHigh = ta.highest(high, breakoutLen)[1]
priorLow = ta.lowest(low, breakoutLen)[1]

trendUp = close > emaTrend and emaSignal > emaTrend and emaTrend > emaTrend[1]
trendDown = close < emaTrend and emaSignal < emaTrend and emaTrend < emaTrend[1]
longMomentum = rsiValue >= 52 and plusDi > minusDi
shortMomentum = rsiValue <= 48 and minusDi > plusDi
longBreakout = close > priorHigh
shortBreakout = close < priorLow
healthyRange = atrValue > syminfo.mintick * 10

float buyScore = 0.0
float sellScore = 0.0

if trendUp
    buyScore += 35
if trendDown
    sellScore += 35
if longMomentum
    buyScore += 25
if shortMomentum
    sellScore += 25
if adxValue >= minAdx and plusDi > minusDi
    buyScore += 15
if adxValue >= minAdx and minusDi > plusDi
    sellScore += 15
if longBreakout
    buyScore += 20
if shortBreakout
    sellScore += 20
if close > emaSignal and close > open
    buyScore += 5
if close < emaSignal and close < open
    sellScore += 5

buyScore := math.min(math.max(buyScore, 0), 100)
sellScore := math.min(math.max(sellScore, 0), 100)
edge = buyScore - sellScore
ready = barstate.isconfirmed and timeframeOk and inSession and healthyRange and not na(priorHigh) and not na(priorLow)

longStopPrice = close - atrValue * atrStopMult
longTargetPrice = close + (close - longStopPrice) * rewardToRisk
shortStopPrice = close + atrValue * atrStopMult
shortTargetPrice = close - (shortStopPrice - close) * rewardToRisk

longSignal = ready and buyScore >= minScore and edge > 0 and strategy.position_size <= 0
shortSignal = ready and sellScore >= minScore and edge < 0 and strategy.position_size >= 0
longMessage = '{"source":"tradingview","action":"BUY","ticker":"' + syminfo.ticker + '","timeframe":"' + timeframe.period + '","price":' + str.tostring(close, format.mintick) + ',"target":' + str.tostring(longTargetPrice, format.mintick) + ',"stop":' + str.tostring(longStopPrice, format.mintick) + ',"qty":' + str.tostring(contractQty) + ',"edge":' + str.tostring(edge, "#.##") + ',"score":' + str.tostring(buyScore, "#.##") + ',"bar_time":"' + str.tostring(time) + '","tag":"' + webhookTag + '"}'
shortMessage = '{"source":"tradingview","action":"SELL","ticker":"' + syminfo.ticker + '","timeframe":"' + timeframe.period + '","price":' + str.tostring(close, format.mintick) + ',"target":' + str.tostring(shortTargetPrice, format.mintick) + ',"stop":' + str.tostring(shortStopPrice, format.mintick) + ',"qty":' + str.tostring(contractQty) + ',"edge":' + str.tostring(edge, "#.##") + ',"score":' + str.tostring(sellScore, "#.##") + ',"bar_time":"' + str.tostring(time) + '","tag":"' + webhookTag + '"}'

var float activeLongStop = na
var float activeLongTarget = na
var float activeShortStop = na
var float activeShortTarget = na

if strategy.position_size == 0 and not longSignal and not shortSignal
    activeLongStop := na
    activeLongTarget := na
    activeShortStop := na
    activeShortTarget := na

if longSignal
    activeLongStop := longStopPrice
    activeLongTarget := longTargetPrice
    activeShortStop := na
    activeShortTarget := na
    strategy.entry("NQ Long", strategy.long, qty=contractQty, alert_message=longMessage)
    alert(longMessage, alert.freq_once_per_bar_close)

if shortSignal
    activeShortStop := shortStopPrice
    activeShortTarget := shortTargetPrice
    activeLongStop := na
    activeLongTarget := na
    strategy.entry("NQ Short", strategy.short, qty=contractQty, alert_message=shortMessage)
    alert(shortMessage, alert.freq_once_per_bar_close)

if not na(activeLongStop) and not na(activeLongTarget)
    strategy.exit("NQ Long Exit", "NQ Long", stop=activeLongStop, limit=activeLongTarget)

if not na(activeShortStop) and not na(activeShortTarget)
    strategy.exit("NQ Short Exit", "NQ Short", stop=activeShortStop, limit=activeShortTarget)

plot(emaTrend, "Trend EMA", color=color.new(color.blue, 0), linewidth=2)
plot(emaSignal, "Signal EMA", color=color.new(color.orange, 0), linewidth=1)
plotshape(longSignal, "BUY", shape.labelup, location.belowbar, color=color.new(color.lime, 0), text="BUY", textcolor=color.black, size=size.tiny)
plotshape(shortSignal, "SELL", shape.labeldown, location.abovebar, color=color.new(color.red, 0), text="SELL", textcolor=color.white, size=size.tiny)
bgcolor(timeframeOk ? na : color.new(color.red, 88), title="Wrong Timeframe Warning")
"""


if __name__ == "__main__":
    print(build_pine_script())
