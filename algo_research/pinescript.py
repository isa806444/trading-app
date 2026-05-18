"""TradingView Pine Script exporter for the clean NQ1 v44 strategy."""


def build_pine_script() -> str:
    return """//@version=6
//@strategy_alert_message {{strategy.order.alert_message}}
strategy("AI NQ1 Smart Paper Bot v44 - Tight Exits", overlay=true, initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=1, pyramiding=0, margin_long=10, margin_short=10, process_orders_on_close=false, calc_on_every_tick=true, calc_on_order_fills=true, commission_type=strategy.commission.cash_per_contract, commission_value=4.0, slippage=2)

// Pro-simple version from the 212 idea list:
// no repainting, confirmed 1H logic, three clean setups, score gate,
// regime/chop/fakeout/location filters, realistic risk, and webhook payloads.

// ---------------- Routing / Safety ----------------
contractQty = input.int(1, "Contracts", minval=1, maxval=10)
signalTimeframe = input.timeframe("60", "Trade Signal Timeframe")
restrictNq = input.bool(true, "Only NQ1 / NQ")
webhookTag = input.string("nq1-smart-paper-v44", "Webhook Tag")
useSessionFilter = input.bool(true, "Use Regular Session")
tradeSession = input.session("0930-1600", "Trading Session")
useNoRepaint = input.bool(true, "Strict No-Repaint")
blockHigherChartTimeframes = input.bool(true, "Block Chart TF Above Signal TF")
useRollingBacktest = input.bool(true, "Rolling Backtest Window")
rollingBacktestYears = input.int(4, "Rolling Backtest Years", minval=1, maxval=20)

// ---------------- Strategy Inputs ----------------
emaFastLen = input.int(50, "Fast EMA", minval=5, maxval=200)
emaSlowLen = input.int(200, "Slow EMA", minval=20, maxval=500)
emaPullbackLen = input.int(21, "EMA Pullback EMA", minval=5, maxval=100)
structureLen = input.int(20, "Structure Lookback", minval=5, maxval=100)
kaufmanLen = input.int(10, "Kaufman Ratio Length", minval=3, maxval=80)
minKaufmanRatio = input.float(0.30, "Minimum Kaufman Ratio", minval=0.05, maxval=1.0, step=0.01)
atrExpansionMult = input.float(1.03, "ATR Expansion Ratio", minval=0.5, maxval=3.0, step=0.01)
dmiLen = input.int(14, "DMI / Trend Strength Length", minval=2, maxval=100)
dmiSmoothing = input.int(14, "DMI Smoothing", minval=2, maxval=100)
minTrendStrength = input.float(15.0, "Minimum Trend Strength", minval=1.0, maxval=60.0, step=0.5)
atrLen = input.int(14, "ATR Length", minval=2, maxval=100)
volumeLen = input.int(30, "Volume Baseline", minval=5, maxval=100)
minRelativeVolume = input.float(0.65, "Minimum Relative Volume", minval=0.1, maxval=5.0, step=0.05)
minBodyPct = input.float(0.25, "Minimum Candle Body %", minval=0.05, maxval=0.95, step=0.05)
pullbackAtrBuffer = input.float(0.35, "EMA Pullback ATR Buffer", minval=0.05, maxval=2.0, step=0.05)
useHtfBias = input.bool(true, "Use Confirmed 4H Bias")
htfConfirmTf = input.timeframe("240", "HTF Bias Timeframe")
minSetupScore = input.float(62.0, "Minimum Setup Score", minval=30.0, maxval=95.0, step=1.0)
targetTradesPerDay = input.int(4, "Target Trades Per Day", minval=0, maxval=10)
maxChopScore = input.float(72.0, "Max Chop Score", minval=20.0, maxval=100.0, step=1.0)

// ---------------- Risk Inputs ----------------
atrStopMult = input.float(1.05, "ATR Stop Multiplier", minval=0.25, maxval=5.0, step=0.05)
rewardMultiple = input.float(2.0, "Reward Multiple", minval=0.5, maxval=10.0, step=0.25)
trailAfterR = input.float(0.45, "Trail After R", minval=0.10, maxval=5.0, step=0.05)
trailAtrMult = input.float(0.85, "Trail ATR Multiplier", minval=0.25, maxval=5.0, step=0.05)
profitGivebackR = input.float(0.35, "Smart Exit Profit Giveback R", minval=0.10, maxval=3.0, step=0.05)
earlyAdverseR = input.float(0.55, "Smart Exit Adverse R", minval=0.10, maxval=3.0, step=0.05)
maxBarsInTrade = input.int(5, "Max Weak Bars In Trade", minval=2, maxval=50)
maxTradesPerDay = input.int(10, "Max Trades Per Day", minval=1, maxval=30)
maxLossesPerDay = input.int(4, "Max Losses Per Day", minval=1, maxval=10)
maxConsecutiveLosses = input.int(3, "Max Consecutive Losses", minval=1, maxval=10)
maxDailyLossDollars = input.float(900.0, "Max Daily Loss ($)", minval=50.0, step=50.0)
cooldownBars = input.int(2, "Cooldown Bars", minval=0, maxval=200)
sessionCloseBufferMinutes = input.int(10, "No New Trades Before Close", minval=0, maxval=60)

// Optional plumbing test. Keep this off unless testing Strategy Report/webhook route.
enableManualTest = input.bool(false, "Enable Manual Plumbing Test")
manualTestAction = input.string("OFF", "Manual Test Action", options=["OFF", "BUY", "SELL", "EXIT_LONG", "EXIT_SHORT"])
enableHourlyWebhookTest = input.bool(false, "Hourly Webhook Test Mode")

// ---------------- Helpers ----------------
clamp(v, lo, hi) =>
    math.min(math.max(v, lo), hi)

scoreBool(cond, points) =>
    cond ? points : 0.0

money(v) =>
    str.tostring(v, format.mintick)

kaufmanRatio(src, len) =>
    directionalChange = math.abs(src[1] - src[len + 1])
    pathLength = 0.0
    for n = 1 to len
        pathLength += math.abs(src[n] - src[n + 1])
    pathLength > 0 ? directionalChange / pathLength : 0.0

// ---------------- Guards ----------------
tickerUpper = str.upper(syminfo.ticker)
isNqSymbol = str.contains(tickerUpper, "NQ") and not str.contains(tickerUpper, "MNQ")
symbolOk = not restrictNq or isNqSymbol
timeframeOk = true
chartSeconds = timeframe.in_seconds(timeframe.period)
signalSeconds = timeframe.in_seconds(signalTimeframe)
chartTimeframeSafe = not blockHigherChartTimeframes or (not na(chartSeconds) and not na(signalSeconds) and chartSeconds <= signalSeconds)
confirmedOk = not useNoRepaint or barstate.isconfirmed
nyHour = hour(time, "America/New_York")
nyMinute = minute(time, "America/New_York")
minutesNow = nyHour * 60 + nyMinute
newDay = ta.change(time("D")) != 0
backtestStartTime = timestamp("America/New_York", year(timenow, "America/New_York") - rollingBacktestYears, month(timenow, "America/New_York"), dayofmonth(timenow, "America/New_York"), 0, 0)
qty = math.max(1, contractQty)

// ---------------- 1H Signal Brain ----------------
// No-future-leak rule: every signal value below uses [1], meaning the bot only
// sees the last fully closed 1H candle. request.security also uses lookahead_off.
signalFrame() =>
    sfOpen = open[1]
    sfHigh = high[1]
    sfLow = low[1]
    sfClose = close[1]
    sfTime = time[1]
    sfEmaFast = ta.ema(close, emaFastLen)[1]
    sfEmaSlow = ta.ema(close, emaSlowLen)[1]
    sfEmaPullback = ta.ema(close, emaPullbackLen)[1]
    sfAtr = ta.atr(atrLen)[1]
    sfAtrBaseline = ta.sma(ta.atr(atrLen), 100)[1]
    sfAtrRatio = sfAtrBaseline > 0 ? sfAtr / sfAtrBaseline : 1.0
    sfVolumeBaseline = ta.sma(volume, volumeLen)[1]
    sfRelativeVolume = sfVolumeBaseline > 0 ? volume[1] / sfVolumeBaseline : 1.0
    sfPriorHigh = ta.highest(high, structureLen)[2]
    sfPriorLow = ta.lowest(low, structureLen)[2]
    sfRangeHigh = ta.highest(high, structureLen)[1]
    sfRangeLow = ta.lowest(low, structureLen)[1]
    sfKaufmanRatio = kaufmanRatio(close, kaufmanLen)
    [sfPlusDiRaw, sfMinusDiRaw, sfTrendRaw] = ta.dmi(dmiLen, dmiSmoothing)
    sfPlusDi = sfPlusDiRaw[1]
    sfMinusDi = sfMinusDiRaw[1]
    sfTrendStrength = sfTrendRaw[1]
    sfVwapRaw = ta.vwap(hlc3)[1]
    sfVwap = na(sfVwapRaw) ? sfEmaPullback : sfVwapRaw
    sfVwapPrior = nz(ta.vwap(hlc3)[4], sfVwap)
    sfVwapSlope = (sfVwap - sfVwapPrior) / math.max(sfAtr, syminfo.mintick)
    sfRange = math.max(sfHigh - sfLow, syminfo.mintick)
    sfBodyPct = math.abs(sfClose - sfOpen) / sfRange
    sfUpperWick = sfHigh - math.max(sfOpen, sfClose)
    sfLowerWick = math.min(sfOpen, sfClose) - sfLow
    sfCloseNearHigh = (sfClose - sfLow) / sfRange >= 0.58
    sfCloseNearLow = (sfHigh - sfClose) / sfRange >= 0.58
    sfOverlap = math.max(0.0, math.min(sfHigh, high[2]) - math.max(sfLow, low[2])) / sfRange
    sfAlternating = (sfClose > sfOpen and close[2] < open[2]) or (sfClose < sfOpen and close[2] > open[2])
    sfBbBasisRaw = ta.sma(close, 20)
    sfBbDevRaw = ta.stdev(close, 20) * 2.0
    sfBbWidthRaw = sfBbBasisRaw != 0 ? (sfBbDevRaw * 2.0) / sfBbBasisRaw * 100.0 : 0.0
    sfBbWidth = sfBbWidthRaw[1]
    sfBbWidthAvg = ta.sma(sfBbWidthRaw, 20)[1]
    sfHour = hour(time, "America/New_York")[1]
    sfMinute = minute(time, "America/New_York")[1]
    sfMinutesNow = sfHour * 60 + sfMinute
    sfInSession = not na(time(timeframe.period, tradeSession)[1])
    sfNearSessionClose = sfInSession and sfMinutesNow >= 960 - sessionCloseBufferMinutes
    [sfOpen, sfHigh, sfLow, sfClose, sfTime, sfEmaFast, sfEmaSlow, sfEmaPullback, sfAtr, sfAtrBaseline, sfAtrRatio, sfRelativeVolume, sfPriorHigh, sfPriorLow, sfRangeHigh, sfRangeLow, sfKaufmanRatio, sfPlusDi, sfMinusDi, sfTrendStrength, sfVwap, sfVwapSlope, sfBodyPct, sfUpperWick, sfLowerWick, sfCloseNearHigh, sfCloseNearLow, sfOverlap, sfAlternating, sfBbWidth, sfBbWidthAvg, sfInSession, sfNearSessionClose]

[signalOpen, signalHigh, signalLow, signalClose, signalTime, emaFast, emaSlow, emaPullback, atrValue, atrBaseline, atrRatio, relativeVolume, priorHigh, priorLow, rangeHigh, rangeLow, kaufmanRatioValue, plusDi, minusDi, trendStrength, vwapValue, vwapSlope, bodyPct, upperWick, lowerWick, closeNearHigh, closeNearLow, candleOverlap, alternatingCandle, bbWidth, bbWidthAvg, signalInSession, signalNearSessionClose] = request.security(syminfo.tickerid, signalTimeframe, signalFrame(), barmerge.gaps_off, barmerge.lookahead_off)
[htfClose, htfEmaFast, htfEmaSlow] = request.security(syminfo.tickerid, htfConfirmTf, [close[1], ta.ema(close, emaFastLen)[1], ta.ema(close, emaSlowLen)[1]], barmerge.gaps_off, barmerge.lookahead_off)

newSignalBar = ta.change(signalTime) != 0
signalReady = not na(signalClose) and not na(emaSlow) and not na(emaPullback) and not na(priorHigh) and not na(priorLow) and not na(atrValue) and not na(kaufmanRatioValue) and not na(trendStrength)
signalSessionOk = not useSessionFilter or signalInSession
backtestWindowOk = not useRollingBacktest or (not na(signalTime) and signalTime >= backtestStartTime)

// ---------------- Daily Risk Memory ----------------
var int tradesToday = 0
var int lossesToday = 0
var int lossStreak = 0
var int reviewedClosedTrades = 0
var int lastTradeBar = na
var int activeEntryBar = na
var float dayStartEquity = strategy.equity
var float activeStop = na
var float activeTarget = na
var float activeRiskPoints = na
var float activeBest = na

if newDay
    tradesToday := 0
    lossesToday := 0
    lossStreak := 0
    dayStartEquity := strategy.equity

if strategy.closedtrades > reviewedClosedTrades
    idx = strategy.closedtrades - 1
    pnl = strategy.closedtrades.profit(idx)
    if pnl < 0
        lossesToday += 1
        lossStreak += 1
    else
        lossStreak := 0
    reviewedClosedTrades := strategy.closedtrades
    lastTradeBar := bar_index

dailyPnL = strategy.equity - dayStartEquity
dailyLossLock = dailyPnL <= -maxDailyLossDollars
lossLock = lossesToday >= maxLossesPerDay or lossStreak >= maxConsecutiveLosses
tradeCountLock = tradesToday >= maxTradesPerDay
cooldownOk = na(lastTradeBar) or bar_index - lastTradeBar > cooldownBars

// ---------------- Pro-Simple Entry Logic ----------------
bullTrend = signalClose > emaSlow and emaFast > emaSlow
bearTrend = signalClose < emaSlow and emaFast < emaSlow
htfBull = not useHtfBias or (htfClose > htfEmaSlow and htfEmaFast >= htfEmaSlow)
htfBear = not useHtfBias or (htfClose < htfEmaSlow and htfEmaFast <= htfEmaSlow)
volumeOk = relativeVolume >= minRelativeVolume
candleOk = bodyPct >= minBodyPct
volatilityOk = na(atrBaseline) or atrBaseline <= 0 or atrValue <= atrBaseline * 2.35
distVwapAtr = math.abs(signalClose - vwapValue) / math.max(atrValue, syminfo.mintick)
rangeSpan = math.max(rangeHigh - rangeLow, syminfo.mintick)
rangePosition = (signalClose - rangeLow) / rangeSpan
middleOfRange = rangePosition > 0.35 and rangePosition < 0.65 and distVwapAtr > 0.45
chopScore = clamp(18.0 + scoreBool(trendStrength < minTrendStrength, 18) + scoreBool(math.abs(vwapSlope) < 0.03, 10) + scoreBool(candleOverlap > 0.52, 12) + scoreBool(alternatingCandle, 8) + scoreBool(bbWidth < bbWidthAvg, 8) + scoreBool(middleOfRange, 10) - scoreBool(kaufmanRatioValue >= minKaufmanRatio and trendStrength >= minTrendStrength, 16), 0.0, 100.0)
reclaimLong = signalLow <= vwapValue and signalClose > vwapValue and signalClose > signalOpen
rejectShort = signalHigh >= vwapValue and signalClose < vwapValue and signalClose < signalOpen
breakLong = signalClose > priorHigh and closeNearHigh and volumeOk
breakShort = signalClose < priorLow and closeNearLow and volumeOk
sweepLong = signalLow < priorLow and signalClose > priorLow and lowerWick > upperWick
sweepShort = signalHigh > priorHigh and signalClose < priorHigh and upperWick > lowerWick
fakeoutLongRisk = signalHigh > priorHigh and signalClose < priorHigh and upperWick > math.max(math.abs(signalClose - signalOpen), syminfo.mintick) * 1.2
fakeoutShortRisk = signalLow < priorLow and signalClose > priorLow and lowerWick > math.max(math.abs(signalClose - signalOpen), syminfo.mintick) * 1.2
doNotChaseLong = distVwapAtr > 2.4 or (signalClose > priorHigh and upperWick > math.max(math.abs(signalClose - signalOpen), syminfo.mintick) * 1.5)
doNotChaseShort = distVwapAtr > 2.4 or (signalClose < priorLow and lowerWick > math.max(math.abs(signalClose - signalOpen), syminfo.mintick) * 1.5)
emaPullbackLong = bullTrend and htfBull and signalLow <= emaPullback + atrValue * pullbackAtrBuffer and signalClose > emaPullback and signalClose > signalOpen and closeNearHigh and volumeOk
emaPullbackShort = bearTrend and htfBear and signalHigh >= emaPullback - atrValue * pullbackAtrBuffer and signalClose < emaPullback and signalClose < signalOpen and closeNearLow and volumeOk
kaufmanLong = bullTrend and htfBull and kaufmanRatioValue >= minKaufmanRatio and signalClose > emaFast and signalClose > signalOpen and volumeOk
kaufmanShort = bearTrend and htfBear and kaufmanRatioValue >= minKaufmanRatio and signalClose < emaFast and signalClose < signalOpen and volumeOk
atrLong = bullTrend and htfBull and atrRatio >= atrExpansionMult and candleOk and closeNearHigh and volumeOk and (breakLong or reclaimLong or signalClose > emaFast)
atrShort = bearTrend and htfBear and atrRatio >= atrExpansionMult and candleOk and closeNearLow and volumeOk and (breakShort or rejectShort or signalClose < emaFast)

longScore = clamp(42.0 + scoreBool(bullTrend, 12) + scoreBool(htfBull, 10) + scoreBool(signalClose > vwapValue, 8) + scoreBool(vwapSlope > 0, 5) + scoreBool(volumeOk, 7) + scoreBool(relativeVolume >= 1.1, 5) + scoreBool(kaufmanRatioValue >= minKaufmanRatio, 8) + scoreBool(trendStrength >= minTrendStrength, 6) + scoreBool(closeNearHigh, 5) + scoreBool(reclaimLong or sweepLong, 8) + scoreBool(emaPullbackLong, 9) + scoreBool(breakLong, 6) - scoreBool(chopScore > maxChopScore, 12) - scoreBool(fakeoutLongRisk, 12) - scoreBool(doNotChaseLong, 10) - scoreBool(bearTrend and htfBear, 12), 0.0, 100.0)
shortScore = clamp(42.0 + scoreBool(bearTrend, 12) + scoreBool(htfBear, 10) + scoreBool(signalClose < vwapValue, 8) + scoreBool(vwapSlope < 0, 5) + scoreBool(volumeOk, 7) + scoreBool(relativeVolume >= 1.1, 5) + scoreBool(kaufmanRatioValue >= minKaufmanRatio, 8) + scoreBool(trendStrength >= minTrendStrength, 6) + scoreBool(closeNearLow, 5) + scoreBool(rejectShort or sweepShort, 8) + scoreBool(emaPullbackShort, 9) + scoreBool(breakShort, 6) - scoreBool(chopScore > maxChopScore, 12) - scoreBool(fakeoutShortRisk, 12) - scoreBool(doNotChaseShort, 10) - scoreBool(bullTrend and htfBull, 12), 0.0, 100.0)
edge = longScore - shortScore
opportunityBoost = targetTradesPerDay > 0 and tradesToday < targetTradesPerDay and minutesNow >= 690 and minutesNow < 930 and lossStreak == 0 ? 6.0 : 0.0
adaptiveMinScore = minSetupScore + scoreBool(chopScore > maxChopScore, 5) + lossStreak * 3.0 - opportunityBoost
chopOk = chopScore <= maxChopScore or sweepLong or sweepShort or emaPullbackLong or emaPullbackShort

baseGate = symbolOk and timeframeOk and chartTimeframeSafe and confirmedOk and signalReady and newSignalBar and backtestWindowOk and signalSessionOk and not signalNearSessionClose and not dailyLossLock and not lossLock and not tradeCountLock and cooldownOk and volatilityOk and chopOk
newLongSignal = baseGate and strategy.position_size == 0 and longScore >= adaptiveMinScore and longScore >= shortScore + 4 and not fakeoutLongRisk and not doNotChaseLong and (kaufmanLong or atrLong or emaPullbackLong or sweepLong)
newShortSignal = baseGate and strategy.position_size == 0 and shortScore >= adaptiveMinScore and shortScore >= longScore + 4 and not fakeoutShortRisk and not doNotChaseShort and (kaufmanShort or atrShort or emaPullbackShort or sweepShort)

testLongSignal = enableManualTest and manualTestAction == "BUY" and chartTimeframeSafe and confirmedOk and backtestWindowOk
testShortSignal = enableManualTest and manualTestAction == "SELL" and chartTimeframeSafe and confirmedOk and backtestWindowOk
testExitLongSignal = enableManualTest and manualTestAction == "EXIT_LONG" and chartTimeframeSafe and confirmedOk and backtestWindowOk
testExitShortSignal = enableManualTest and manualTestAction == "EXIT_SHORT" and chartTimeframeSafe and confirmedOk and backtestWindowOk
hourlySlot = signalReady ? int(signalTime / 3600000) : 0
hourlyTestReady = enableHourlyWebhookTest and symbolOk and chartTimeframeSafe and confirmedOk and signalReady and newSignalBar and backtestWindowOk and signalSessionOk
hourlyTestLongSignal = hourlyTestReady and hourlySlot % 2 == 0
hourlyTestShortSignal = hourlyTestReady and hourlySlot % 2 != 0

longSignal = hourlyTestLongSignal or (strategy.position_size == 0 and (newLongSignal or testLongSignal))
shortSignal = hourlyTestShortSignal or (strategy.position_size == 0 and (newShortSignal or testShortSignal))

// ---------------- Stop / Target Plan ----------------
longAtrStop = signalClose - atrValue * atrStopMult
shortAtrStop = signalClose + atrValue * atrStopMult
longStructureStop = na(priorLow) ? longAtrStop : priorLow - syminfo.mintick * 2
shortStructureStop = na(priorHigh) ? shortAtrStop : priorHigh + syminfo.mintick * 2
testModeActive = enableManualTest or enableHourlyWebhookTest
plannedLongStopRaw = testModeActive ? signalClose - atrValue * 0.75 : math.max(longAtrStop, longStructureStop)
plannedShortStopRaw = testModeActive ? signalClose + atrValue * 0.75 : math.min(shortAtrStop, shortStructureStop)
plannedLongStop = math.min(plannedLongStopRaw, signalClose - syminfo.mintick * 4)
plannedShortStop = math.max(plannedShortStopRaw, signalClose + syminfo.mintick * 4)
plannedLongRisk = math.max(signalClose - plannedLongStop, syminfo.mintick)
plannedShortRisk = math.max(plannedShortStop - signalClose, syminfo.mintick)
plannedLongTarget = signalClose + plannedLongRisk * rewardMultiple
plannedShortTarget = signalClose - plannedShortRisk * rewardMultiple

longSetup = enableHourlyWebhookTest ? "HourlyWebhookTest" : kaufmanLong ? "KaufmanRatio" : atrLong ? "ATRExpansion" : emaPullbackLong ? "EMAPullback" : sweepLong ? "EMAPullback" : "ManualTest"
shortSetup = enableHourlyWebhookTest ? "HourlyWebhookTest" : kaufmanShort ? "KaufmanRatio" : atrShort ? "ATRExpansion" : emaPullbackShort ? "EMAPullback" : sweepShort ? "EMAPullback" : "ManualTest"
longGrade = longScore >= 84 ? "A+" : longScore >= 74 ? "A" : "B"
shortGrade = shortScore >= 84 ? "A+" : shortScore >= 74 ? "A" : "B"
riskMode = enableHourlyWebhookTest ? "WebhookTestHourly" : "ProSimpleTight"
testModeJson = enableHourlyWebhookTest ? "true" : "false"
longReason = enableHourlyWebhookTest ? "Hourly webhook route test only | alternates every confirmed 1H bar" : longSetup + " | score " + str.tostring(longScore, "#") + " | KR " + str.tostring(kaufmanRatioValue, "#.##") + " | ATR " + str.tostring(atrRatio, "#.##") + "x | chop " + str.tostring(chopScore, "#") + " | tight trail/smart exits"
shortReason = enableHourlyWebhookTest ? "Hourly webhook route test only | alternates every confirmed 1H bar" : shortSetup + " | score " + str.tostring(shortScore, "#") + " | KR " + str.tostring(kaufmanRatioValue, "#.##") + " | ATR " + str.tostring(atrRatio, "#.##") + "x | chop " + str.tostring(chopScore, "#") + " | tight trail/smart exits"

longMessage = '{"source":"tradingview","action":"BUY","ticker":"' + syminfo.ticker + '","timeframe":"' + signalTimeframe + '","chart_timeframe":"' + timeframe.period + '","price":' + money(signalClose) + ',"target":' + money(plannedLongTarget) + ',"profit_mode":"BRACKET_TARGET_TIGHT_TRAIL","stop_mode":"SMART_TRAILING_STOP","stop":' + money(plannedLongStop) + ',"qty":' + str.tostring(qty) + ',"edge":' + str.tostring(edge, "#.##") + ',"score":' + str.tostring(longScore, "#.##") + ',"ai_score":' + str.tostring(longScore, "#.##") + ',"ai_bias":"LONG","entry_style":"' + longSetup + '","grade":"' + longGrade + '","risk_mode":"' + riskMode + '","test_mode":' + testModeJson + ',"reason":"' + longReason + '","bar_time":"' + str.tostring(signalTime) + '","tag":"' + webhookTag + '"}'
shortMessage = '{"source":"tradingview","action":"SELL","ticker":"' + syminfo.ticker + '","timeframe":"' + signalTimeframe + '","chart_timeframe":"' + timeframe.period + '","price":' + money(signalClose) + ',"target":' + money(plannedShortTarget) + ',"profit_mode":"BRACKET_TARGET_TIGHT_TRAIL","stop_mode":"SMART_TRAILING_STOP","stop":' + money(plannedShortStop) + ',"qty":' + str.tostring(qty) + ',"edge":' + str.tostring(edge, "#.##") + ',"score":' + str.tostring(shortScore, "#.##") + ',"ai_score":' + str.tostring(shortScore, "#.##") + ',"ai_bias":"SHORT","entry_style":"' + shortSetup + '","grade":"' + shortGrade + '","risk_mode":"' + riskMode + '","test_mode":' + testModeJson + ',"reason":"' + shortReason + '","bar_time":"' + str.tostring(signalTime) + '","tag":"' + webhookTag + '"}'
exitLongMessage = '{"source":"tradingview","action":"EXIT_LONG","ticker":"' + syminfo.ticker + '","timeframe":"' + signalTimeframe + '","chart_timeframe":"' + timeframe.period + '","price":' + money(signalClose) + ',"qty":' + str.tostring(qty) + ',"edge":' + str.tostring(edge, "#.##") + ',"score":' + str.tostring(longScore, "#.##") + ',"risk_mode":"' + riskMode + '","test_mode":' + testModeJson + ',"reason":"tight smart exit","bar_time":"' + str.tostring(signalTime) + '","tag":"' + webhookTag + '"}'
exitShortMessage = '{"source":"tradingview","action":"EXIT_SHORT","ticker":"' + syminfo.ticker + '","timeframe":"' + signalTimeframe + '","chart_timeframe":"' + timeframe.period + '","price":' + money(signalClose) + ',"qty":' + str.tostring(qty) + ',"edge":' + str.tostring(edge, "#.##") + ',"score":' + str.tostring(shortScore, "#.##") + ',"risk_mode":"' + riskMode + '","test_mode":' + testModeJson + ',"reason":"tight smart exit","bar_time":"' + str.tostring(signalTime) + '","tag":"' + webhookTag + '"}'

// ---------------- Strategy Report + Webhook Orders ----------------
if longSignal
    tradesToday += 1
    lastTradeBar := bar_index
    activeEntryBar := bar_index
    activeStop := plannedLongStop
    activeTarget := plannedLongTarget
    activeRiskPoints := plannedLongRisk
    activeBest := signalClose
    strategy.entry("Long", strategy.long, qty=qty, alert_message=longMessage)
    alert(longMessage, alert.freq_once_per_bar_close)

if shortSignal
    tradesToday += 1
    lastTradeBar := bar_index
    activeEntryBar := bar_index
    activeStop := plannedShortStop
    activeTarget := plannedShortTarget
    activeRiskPoints := plannedShortRisk
    activeBest := signalClose
    strategy.entry("Short", strategy.short, qty=qty, alert_message=shortMessage)
    alert(shortMessage, alert.freq_once_per_bar_close)

barsInTrade = not na(activeEntryBar) ? bar_index - activeEntryBar : 0
longHealth = clamp(longScore - shortScore + 55.0 - scoreBool(signalClose < emaPullback, 8) - scoreBool(signalClose < vwapValue, 10) - scoreBool(closeNearLow, 6), 0.0, 100.0)
shortHealth = clamp(shortScore - longScore + 55.0 - scoreBool(signalClose > emaPullback, 8) - scoreBool(signalClose > vwapValue, 10) - scoreBool(closeNearHigh, 6), 0.0, 100.0)
safeRiskPoints = math.max(nz(activeRiskPoints, atrValue), syminfo.mintick)
longOpenR = strategy.position_size > 0 ? (signalClose - strategy.position_avg_price) / safeRiskPoints : 0.0
shortOpenR = strategy.position_size < 0 ? (strategy.position_avg_price - signalClose) / safeRiskPoints : 0.0
longBestR = strategy.position_size > 0 and not na(activeBest) ? (activeBest - strategy.position_avg_price) / safeRiskPoints : 0.0
shortBestR = strategy.position_size < 0 and not na(activeBest) ? (strategy.position_avg_price - activeBest) / safeRiskPoints : 0.0
longGivebackR = math.max(longBestR - longOpenR, 0.0)
shortGivebackR = math.max(shortBestR - shortOpenR, 0.0)

if strategy.position_size > 0 and signalReady
    activeBest := na(activeBest) ? signalHigh : math.max(activeBest, signalHigh)
    openProfitPoints = signalClose - strategy.position_avg_price
    if openProfitPoints >= safeRiskPoints * trailAfterR
        structureTrail = signalClose - atrValue * trailAtrMult
        profitLockStop = strategy.position_avg_price + safeRiskPoints * 0.05
        activeStop := math.max(math.max(activeStop, structureTrail), profitLockStop)
    strategy.exit("Long Risk Exit", from_entry="Long", stop=activeStop, limit=activeTarget, alert_message=exitLongMessage)

if strategy.position_size < 0 and signalReady
    activeBest := na(activeBest) ? signalLow : math.min(activeBest, signalLow)
    openProfitPoints = strategy.position_avg_price - signalClose
    if openProfitPoints >= safeRiskPoints * trailAfterR
        structureTrail = signalClose + atrValue * trailAtrMult
        profitLockStop = strategy.position_avg_price - safeRiskPoints * 0.05
        activeStop := math.min(math.min(activeStop, structureTrail), profitLockStop)
    strategy.exit("Short Risk Exit", from_entry="Short", stop=activeStop, limit=activeTarget, alert_message=exitShortMessage)

longAdverseExit = longOpenR <= -earlyAdverseR and (shortScore > longScore or signalClose < vwapValue or closeNearLow)
shortAdverseExit = shortOpenR <= -earlyAdverseR and (longScore > shortScore or signalClose > vwapValue or closeNearHigh)
longGivebackExit = longBestR >= 0.70 and longGivebackR >= profitGivebackR and (longHealth < 66 or shortScore > longScore)
shortGivebackExit = shortBestR >= 0.70 and shortGivebackR >= profitGivebackR and (shortHealth < 66 or longScore > shortScore)
longNoFollowThroughExit = barsInTrade >= 2 and longOpenR < 0.20 and longHealth < 60
shortNoFollowThroughExit = barsInTrade >= 2 and shortOpenR < 0.20 and shortHealth < 60
longThesisExit = strategy.position_size > 0 and confirmedOk and newSignalBar and (longAdverseExit or longGivebackExit or longNoFollowThroughExit or (signalClose < emaPullback and longHealth < 62) or (signalClose < vwapValue and longHealth < 58) or shortScore > longScore + 8 or barsInTrade >= maxBarsInTrade and longHealth < 66 or testExitLongSignal)
shortThesisExit = strategy.position_size < 0 and confirmedOk and newSignalBar and (shortAdverseExit or shortGivebackExit or shortNoFollowThroughExit or (signalClose > emaPullback and shortHealth < 62) or (signalClose > vwapValue and shortHealth < 58) or longScore > shortScore + 8 or barsInTrade >= maxBarsInTrade and shortHealth < 66 or testExitShortSignal)

if longThesisExit
    strategy.close("Long", alert_message=exitLongMessage)
    alert(exitLongMessage, alert.freq_once_per_bar_close)

if shortThesisExit
    strategy.close("Short", alert_message=exitShortMessage)
    alert(exitShortMessage, alert.freq_once_per_bar_close)

if strategy.position_size == 0 and not longSignal and not shortSignal
    activeStop := na
    activeTarget := na
    activeRiskPoints := na
    activeBest := na
    activeEntryBar := na

// ---------------- Visuals / Status ----------------
plot(emaFast, "Fast EMA", color=color.new(color.orange, 0), linewidth=1)
plot(emaPullback, "Pullback EMA", color=color.new(color.white, 0), linewidth=1)
plot(emaSlow, "Slow EMA", color=color.new(color.blue, 0), linewidth=2)
plot(vwapValue, "VWAP", color=color.new(color.aqua, 15), linewidth=1)
plot(priorHigh, "Structure High", color=color.new(color.lime, 78), style=plot.style_linebr)
plot(priorLow, "Structure Low", color=color.new(color.red, 78), style=plot.style_linebr)
plot(activeStop, "Active Stop", color=color.new(color.red, 0), style=plot.style_linebr)
plot(activeTarget, "Active Target", color=color.new(color.lime, 0), style=plot.style_linebr)

plotshape(longSignal, title="Pro Long", style=shape.labelup, location=location.belowbar, color=color.new(color.lime, 0), text="BUY", textcolor=color.black, size=size.small)
plotshape(shortSignal, title="Pro Short", style=shape.labeldown, location=location.abovebar, color=color.new(color.red, 0), text="SELL", textcolor=color.white, size=size.small)
plotshape(longThesisExit, title="Pro Exit Long", style=shape.xcross, location=location.abovebar, color=color.new(color.yellow, 0), text="EXIT", textcolor=color.black, size=size.tiny)
plotshape(shortThesisExit, title="Pro Exit Short", style=shape.xcross, location=location.belowbar, color=color.new(color.yellow, 0), text="EXIT", textcolor=color.black, size=size.tiny)

statusText = not symbolOk ? "Wrong symbol" : not chartTimeframeSafe ? "Chart TF too high" : not signalReady ? "Building 1H data" : not backtestWindowOk ? "Before 4Y window" : not signalSessionOk ? "Outside 1H session" : dailyLossLock ? "Daily loss lock" : lossLock ? "Loss lock" : tradeCountLock ? "Trade cap" : signalNearSessionClose ? "1H close buffer" : "No-leak 1H armed"
positionText = strategy.position_size > 0 ? "Long" : strategy.position_size < 0 ? "Short" : "Flat"
setupText = kaufmanLong or kaufmanShort ? "Kaufman" : atrLong or atrShort ? "ATR" : emaPullbackLong or emaPullbackShort ? "EMA Pullback" : "Waiting"
var table dash = table.new(position.top_right, 2, 9, bgcolor=color.new(color.black, 12), border_color=color.new(color.white, 70), border_width=1)
if barstate.islast
    table.cell(dash, 0, 0, "NQ v44 Pro", text_color=color.white, bgcolor=color.new(color.teal, 58))
    table.cell(dash, 1, 0, statusText, text_color=baseGate ? color.lime : color.orange, bgcolor=color.new(color.teal, 58))
    table.cell(dash, 0, 1, "Position", text_color=color.silver)
    table.cell(dash, 1, 1, positionText, text_color=strategy.position_size == 0 ? color.white : color.lime)
    table.cell(dash, 0, 2, "Setup", text_color=color.silver)
    table.cell(dash, 1, 2, setupText, text_color=newLongSignal ? color.lime : newShortSignal ? color.orange : color.silver)
    table.cell(dash, 0, 3, "Score L/S", text_color=color.silver)
    table.cell(dash, 1, 3, str.tostring(longScore, "#") + "/" + str.tostring(shortScore, "#"), text_color=edge >= 0 ? color.lime : color.orange)
    table.cell(dash, 0, 4, "Chop", text_color=color.silver)
    table.cell(dash, 1, 4, str.tostring(chopScore, "#"), text_color=chopScore <= maxChopScore ? color.lime : color.orange)
    table.cell(dash, 0, 5, "Today", text_color=color.silver)
    table.cell(dash, 1, 5, str.tostring(tradesToday) + "/" + str.tostring(maxTradesPerDay) + " target " + str.tostring(targetTradesPerDay), text_color=tradeCountLock ? color.red : color.white)
    table.cell(dash, 0, 6, "Daily P/L", text_color=color.silver)
    table.cell(dash, 1, 6, str.tostring(dailyPnL, "#.##"), text_color=dailyPnL >= 0 ? color.lime : color.red)
    table.cell(dash, 0, 7, "Route", text_color=color.silver)
    table.cell(dash, 1, 7, "TV > Render > App", text_color=color.white)
    table.cell(dash, 0, 8, "Tag", text_color=color.silver)
    table.cell(dash, 1, 8, webhookTag + " | no leak", text_color=color.lime)

bgcolor(symbolOk and timeframeOk ? na : color.new(color.red, 88))
"""


if __name__ == "__main__":
    print(build_pine_script())
