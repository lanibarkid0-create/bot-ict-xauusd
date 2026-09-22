"""
Unified Multi-Timeframe Technical Analysis
==========================================
Menggabungkan 3 repo (Vibe-Trading, FinceptTerminal, AutoHedge) 
dalam SATU analisis teknikal yang powerfull dengan SATU zona entry.

Mode:
  - scalp   : HTF=M15, LTF=M5 (M1 jika ada), SL 50 / TP 100/150
  - intraday : HTF=H1,   LTF=M5,                   SL 80 / TP 160/240
  - swing   : HTF=D1,   LTF=H1,                    SL 200 / TP 400/600

Output: UnifiedAnalysisResult (objek tunggal berisi semua informasi)
        + report Telegram siap kirim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import datetime


# Import 3 repo yang sudah ada
from vibe_trading import vibe_analyze as vibe_analyze_fn
from fincept_terminal import fincept_analyze as fincept_analyze_fn
from autohedge import (
    director_agent,
    quant_agent,
    risk_agent,
    execution_agent,
)

# -------------------------------------------------------
# Konfigurasi per mode
# -------------------------------------------------------
SL_PTS_BY_MODE: dict[str, float] = {
    'scalp': 50.0,
    'intraday': 80.0,
    'swing': 200.0,
}
TP1_PTS_BY_MODE: dict[str, float] = {
    'scalp': 100.0,
    'intraday': 160.0,
    'swing': 400.0,
}
TP2_PTS_BY_MODE: dict[str, float] = {
    'scalp': 150.0,
    'intraday': 240.0,
    'swing': 600.0,
}
HTF_BY_MODE: dict[str, str] = {
    'scalp': 'M15',
    'intraday': 'H1',
    'swing': 'D1',
}
LTF_BY_MODE: dict[str, str] = {
    'scalp': 'M5',
    'intraday': 'M5',
    'swing': 'H1',
}
EQUITY_BY_MODE: dict[str, float] = {
    'scalp': 10000.0,
    'intraday': 25000.0,
    'swing': 50000.0,
}
MODE_FULL: dict[str, str] = {
    'scalp': 'SCALPING',
    'intraday': 'INTRADAY',
    'swing': 'SWING',
}
MODE_ICON: dict[str, str] = {
    'scalp': '⚡',
    'intraday': '📈',
    'swing': '🌊',
}


# -------------------------------------------------------
# Data classes
# -------------------------------------------------------
@dataclass
class HTFAnalysis:
    timeframe: str
    vibe: dict
    fincept: dict
    director: dict
    bias: str
    conviction: float


@dataclass
class LTFAnalysis:
    timeframe: str
    vibe: dict
    quant: dict
    risk: dict
    execution: dict
    entry_limit: float
    sl: float
    tp1: float
    tp2: float
    lot: float


@dataclass
class UnifiedAnalysisResult:
    mode: str
    spot: float
    timestamp: str
    htf: HTFAnalysis
    ltf: LTFAnalysis
    direction: str
    entry_limit: float
    sl: float
    tp1: float
    tp2: float
    lot: float
    confidence: float
    confluence_factors: list[str]
    report: str

# Helper functions
def _refine_entry(entry, direction, confluence_strength, atr, spot):
    """Refine entry price based on confluence strength."""
    if confluence_strength < 0.3:
        return entry
    refinement = atr * 0.1 * (1 - confluence_strength)
    if direction == 'BUY':
        return round(spot - refinement, 2)
    return round(spot + refinement, 2)


def _compute_confluence_strength(htf_bias, vibe, quant, risk, direction):
    """Calculate confluence strength 0.0-1.0."""
    if not direction or direction == 'WAIT':
        return 0.0
    score = 0.0
    # Vibe score (0-100) → 0-1
    vibe_score = vibe.get('score_0_100', 50) / 100.0
    score += vibe_score * 0.25
    # Risk lots (proxy confidence)
    lot = risk.get('lots', 0)
    score += min(1.0, lot / 1.0) * 0.15  # 1 lot = max confluence
    # Agreement between HTF bias and Vibe LTF bias
    ltf_bias = vibe.get('bias', 'NEUTRAL')
    if htf_bias == ltf_bias and htf_bias in ('BUY', 'SELL'):
        score += 0.4
    elif htf_bias != ltf_bias and htf_bias in ('BUY', 'SELL'):
        score += 0.1  # disagreement → reduce
    # Volatility reasonable
    vol = quant.get('vol_daily', 0)
    if 0.02 < vol < 1.5:
        score += 0.1
    return min(1.0, max(0.0, score))


# Unified Analyzer class
class UnifiedAnalyzer:
    """Multi-timeframe analyzer combining 3 repos into ONE entry zone."""

    def __init__(self):
        pass

    def analyze(self, mode, hist):
        """Analyze: return UnifiedAnalysisResult with ONE entry zone."""
        if mode not in SL_PTS_BY_MODE:
            raise ValueError(f'Mode: {mode}')
        htf_tf = HTF_BY_MODE[mode]
        ltf_tf = LTF_BY_MODE[mode]
        sl_pts = SL_PTS_BY_MODE[mode]
        tp1_pts = TP1_PTS_BY_MODE[mode]
        tp2_pts = TP2_PTS_BY_MODE[mode]
        equity = EQUITY_BY_MODE[mode]

        htf_hist = hist.get(htf_tf, [])
        ltf_hist = hist.get(ltf_tf, [])
        m1_hist = hist.get('M1', [])

        actual_ltf_tf = ltf_tf
        ltf_hist_for_analysis = ltf_hist
        if mode == 'scalp' and m1_hist:
            actual_ltf_tf = 'M1'
            ltf_hist_for_analysis = m1_hist

        htf = self._analyze_htf(htf_tf, htf_hist)
        ltf = self._analyze_ltf(actual_ltf_tf, ltf_hist_for_analysis, htf,
                                sl_pts, tp1_pts, tp2_pts, equity)
        return self._synthesize(htf, ltf, mode)

    def _analyze_htf(self, tf, hist):
        """Analyze HTF using Vibe-Trading + FinceptTerminal + AutoHedge Director."""
        spot = hist[-1]['close'] if hist else 0.0
        vibe = vibe_analyze_fn(hist, spot) if hist else {}
        fincept = fincept_analyze_fn(hist, spot) if hist else {}
        closes = [float(r['close']) for r in hist] if hist else []
        director = director_agent(spot, closes, vibe, fincept) if hist else {}
        vb = vibe.get('bias', 'NEUTRAL')
        db = director.get('vibe_bias', 'NEUTRAL')
        bias = vb if vb != 'NEUTRAL' else db
        conviction = director.get('conviction', 50)
        return HTFAnalysis(tf, vibe, fincept, director, bias, conviction)

    def _analyze_ltf(self, tf, hist, htf, sl_pts, tp1_pts, tp2_pts, equity):
        """Analyze LTF using Vibe-Trading + AutoHedge Quant/Risk/Execution."""
        spot = hist[-1]['close'] if hist else htf.vibe.get('spot_price', 0)
        vibe = vibe_analyze_fn(hist, spot) if hist else {}
        quant = quant_agent(hist, spot) if hist else {}
        direction = htf.bias
        risk = risk_agent(spot, direction, quant, equity=equity, sl_pts=sl_pts)
        exec_order = execution_agent(direction, spot, sl_pts, tp1_pts, tp2_pts, risk)
        confluence = _compute_confluence_strength(htf.bias, vibe, quant, risk, direction)
        entry_limit = _refine_entry(
            exec_order.get('entry', spot), direction, confluence,
            vibe.get('atr14', 0), spot)
        return LTFAnalysis(
            tf, vibe, quant, risk, exec_order,
            entry_limit,
            exec_order.get('sl', spot - sl_pts if direction == 'BUY' else spot + sl_pts),
            exec_order.get('tp1', spot + tp1_pts if direction == 'BUY' else spot - tp1_pts),
            exec_order.get('tp2', spot + tp2_pts if direction == 'BUY' else spot - tp2_pts),
            risk.get('lots', 0)
        )

    def _synthesize(self, htf, ltf, mode):
        """Synthesize HTF + LTF into ONE final decision."""
        direction = htf.bias if htf.bias in ('BUY', 'SELL') else 'WAIT'
        if direction == 'WAIT':
            factors = ['HTF bias NETRAL -- tunggu konfirmasi']
            report = self._report_wait(htf, ltf, mode, factors)
            return UnifiedAnalysisResult(
                mode=mode,
                spot=htf.vibe.get('spot_price', 0),
                timestamp=datetime.datetime.now().isoformat(),
                htf=htf, ltf=ltf, direction='WAIT',
                entry_limit=0, sl=0, tp1=0, tp2=0, lot=0,
                confidence=0, confluence_factors=factors, report=report
            )
        confluence = _compute_confluence_strength(htf.bias, ltf.vibe, ltf.quant, ltf.risk, direction)
        confidence = min(100, htf.conviction * 0.4 + ltf.vibe.get('score_0_100', 50) * 0.3 + confluence * 30)
        factors = []
        ema9 = htf.vibe.get('ema9', 0)
        ema21 = htf.vibe.get('ema21', 0)
        if ema9:
            factors.append(f'HTF {htf.timeframe}: {htf.bias} (Vibe EMA {ema9}/{ema21})')
        else:
            factors.append(f'HTF {htf.timeframe}: {htf.bias}')
        dcf_v = htf.fincept.get('dcf', {}).get('verdict', 'FAIR')
        dcf_g = htf.fincept.get('dcf', {}).get('gap_pct', 0)
        if dcf_v == 'UNDERVALUED':
            factors.append(f'Fincept DCF: UNDERVALUED (gap {dcf_g:+}%)')
        elif dcf_v == 'OVERVALUED':
            factors.append(f'Fincept DCF: OVERVALUED (gap {dcf_g:+}%)')
        else:
            factors.append(f'Fincept DCF: FAIR (gap {dcf_g:+}%)')
        sv = htf.fincept.get('sharpe_var', {})
        factors.append(f'Fincept Risk: Sharpe {sv.get("sharpe_daily", 0)}, VaR95 {sv.get("var95_daily_pct", 0)}%')
        thesis = htf.director.get('thesis', '')
        strat = htf.director.get('strategy', '')
        factors.append(f'AutoHedge Director: {thesis} -> {strat}')
        ltf_bias2 = ltf.vibe.get('bias', 'NEUTRAL')
        ltf_score2 = ltf.vibe.get('score_0_100', 0)
        factors.append(f'LTF {ltf.timeframe}: {ltf_bias2} (score {ltf_score2}/100)')
        order = ltf.execution.get('order', '')
        factors.append(f'AutoHedge Execution: {order}')
        risk_note = ltf.risk.get('note', '')
        factors.append(f'AutoHedge Risk: {risk_note}')
        spot_val = ltf.vibe.get('spot_price', 0)
        entry_limit = _refine_entry(ltf.entry_limit, direction, confluence,
                                     ltf.vibe.get('atr14', 0), spot_val)
        result = {
            'entry_limit': entry_limit,
            'sl': ltf.sl, 'tp1': ltf.tp1, 'tp2': ltf.tp2,
            'lot': ltf.lot, 'confidence': confidence, 'factors': factors
        }
        report = self._report(htf, ltf, direction, result, mode)
        return UnifiedAnalysisResult(
            mode=mode,
            spot=spot_val,
            timestamp=datetime.datetime.now().isoformat(),
            htf=htf, ltf=ltf, direction=direction,
            entry_limit=entry_limit, sl=ltf.sl, tp1=ltf.tp1,
            tp2=ltf.tp2, lot=ltf.lot, confidence=confidence,
            confluence_factors=factors, report=report
        )



    def _report(self, htf, ltf, direction, result, mode):
        """Generate Telegram report for BUY/SELL signal."""
        spot = htf.vibe.get('spot_price', 0) or result.get('entry_limit', 0)
        icon = MODE_ICON.get(mode, '') + ' '
        full = MODE_FULL.get(mode, mode.upper())
        lines = []
        lines.append(f'{icon}*ANALISIS TERPADU {full} XAUUSD*')
        lines.append(f'💵 Spot: *{spot:,.2f}*')
        lines.append(f'⏰ Timestamp: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB")}')
        lines.append(f'📋 Mode: *{full}* (HTF={htf.timeframe}, LTF={ltf.timeframe})')
        lines.append('')
        lines.append(f'📊 *HTF {htf.timeframe.upper()} - DETEKSI BIAS & DASAR*')
        lines.append(f'• *Vibe-Trading* (EMA/RSI/MACD/BB/ATR):')
        e9 = htf.vibe.get('ema9', 0)
        e21 = htf.vibe.get('ema21', 0)
        rsi = htf.vibe.get('rsi14', 0)
        macd_hist = htf.vibe.get('macd', {}).get('hist', 0)
        atr14 = htf.vibe.get('atr14', 0)
        score100 = htf.vibe.get('score_0_100', 0)
        lines.append(f'  EMA {e9:,.2f} / {e21:,.2f} | RSI {rsi} | MACD {macd_hist:+} | ATR {atr14} | Skor {score100}/100')
        lines.append(f'• *FinceptTerminal* (DCF + Risiko):')
        dcf_v = htf.fincept.get('dcf', {}).get('verdict', 'FAIR')
        dcf_g = htf.fincept.get('dcf', {}).get('gap_pct', 0)
        sv = htf.fincept.get('sharpe_var', {})
        sharpe = sv.get('sharpe_daily', 0)
        var95 = sv.get('var95_daily_pct', 0)
        lines.append(f'  DCF: *{dcf_v}* (gap {dcf_g:+}%) | Sharpe: {sharpe} | VaR95: {var95}%')
        lines.append(f'• *AutoHedge Director*: {htf.director.get("thesis", "-")}')
        lines.append(f'  Strategi: {htf.director.get("strategy", "-")} | Conviction: {htf.conviction}%')
        lines.append(f'• *Arah HTF: *{htf.bias.upper()}*')
        lines.append('')
        lines.append(f'📈 *LTF {ltf.timeframe.upper()} - ZONA ENTRY & EKSEKUSI*')
        lines.append(f'• *Vibe-Trading* (EMA/RSI/MACD):')
        le9 = ltf.vibe.get('ema9', 0)
        le21 = ltf.vibe.get('ema21', 0)
        lrsi = ltf.vibe.get('rsi14', 0)
        lm_hist = ltf.vibe.get('macd', {}).get('hist', 0)
        lscore = ltf.vibe.get('score_0_100', 0)
        lines.append(f'  EMA {le9:,.2f} / {le21:,.2f} | RSI {lrsi} | MACD {lm_hist:+} | Skor {lscore}/100')
        lines.append(f'• *AutoHedge Execution*: {ltf.execution.get("order", "-")}')
        lines.append('')
        lines.append(f'⚡ *SATU ZONE ENTRY (KONFLUENSI 3 REPO)*')
        lines.append(f'• *Arah: *{direction.upper()}*')
        sl_pts = SL_PTS_BY_MODE.get(mode, 50)
        tp1_pts = TP1_PTS_BY_MODE.get(mode, 100)
        tp2_pts = TP2_PTS_BY_MODE.get(mode, 150)
        lines.append(f'• *Entry Limit: *{result["entry_limit"]:,.2f}*')
        lines.append(f'• *SL: *{result["sl"]:,.2f}* ({sl_pts:.0f} pip = {sl_pts*100:.0f} poin)')
        lines.append(f'• *TP1: *{result["tp1"]:,.2f}* ({tp1_pts:.0f} pip, R:R {result["tp1"]/result["sl"]:.1f}:1)')
        lines.append(f'• *TP2: *{result["tp2"]:,.2f}* ({tp2_pts:.0f} pip, R:R {result["tp2"]/result["sl"]:.1f}:1)')
        lines.append(f'• *Lot: *{result["lot"]} lot* (XAUUSD 100 oz/lot)')
        lines.append(f'• *Confiden: *{result["confidence"]}%*')
        lines.append('')
        lines.append(f'📋 *FAKTOR KONFLUENSI ({len(result["factors"])} faktor):*')
        for f in result['factors']:
            lines.append(f'  • {f}')
        lines.append('')
        lines.append(f'💡 *KETERANGAN:* HTF {htf.timeframe} memberikan arah ({htf.bias}), LTF {ltf.timeframe} memberikan zona entry. 3 repo digabung jadi SATU keputusan.')
        lines.append('')
        lines.append('⚠️ Disclaimer: Analisis untuk edukasiのみ, bukan saran keuangan. Lakukan risk management sendiri.')
        return chr(10).join(lines)

    def _report_wait(self, htf, ltf, mode, factors):
        """Generate Telegram report for WAIT signal."""
        spot = htf.vibe.get('spot_price', 0) or 0
        icon = MODE_ICON.get(mode, '') + ' '
        full = MODE_FULL.get(mode, mode.upper())
        lines = []
        lines.append(f'{icon}*ANALISIS TERPADU {full} XAUUSD*')
        lines.append(f'💵 Spot: *{spot:,.2f}*')
        lines.append(f'📋 Mode: *{full}* (HTF={htf.timeframe}, LTF={ltf.timeframe})')
        lines.append('')
        lines.append(f'📊 *HTF {htf.timeframe.upper()} - DETEKSI BIAS*')
        e9 = htf.vibe.get('ema9', 0)
        e21 = htf.vibe.get('ema21', 0)
        rsi = htf.vibe.get('rsi14', 0)
        score100 = htf.vibe.get('score_0_100', 0)
        lines.append(f'• Vibe-Trading: EMA {e9:,.2f}/{e21:,.2f} | RSI {rsi} | Skor {score100}/100')
        dcf_v = htf.fincept.get('dcf', {}).get('verdict', 'FAIR')
        dcf_g = htf.fincept.get('dcf', {}).get('gap_pct', 0)
        lines.append(f'• FinceptTerminal: DCF {dcf_v} (gap {dcf_g:+}%)')
        lines.append(f'• AutoHedge Director: {htf.director.get("thesis", "-")}')
        lines.append(f'• *Arah HTF: NETRAL* (tidak ada bias jelas)')
        lines.append('')
        lines.append(f'📈 *LTF {ltf.timeframe.upper()} - TUNGGU KONFIRMASI*')
        le9 = ltf.vibe.get('ema9', 0)
        le21 = ltf.vibe.get('ema21', 0)
        lrsi = ltf.vibe.get('rsi14', 0)
        lscore = ltf.vibe.get('score_0_100', 0)
        lines.append(f'• Vibe-Trading LTF: EMA {le9:,.2f}/{le21:,.2f} | RSI {lrsi} | Skor {lscore}/100')
        lines.append('')
        lines.append(f'📋 *FAKTOR:*')
        for f in factors:
            lines.append(f'  • {f}')
        lines.append('')
        lines.append(f'💡 *KESIMPULAN:* HTF belum memberikan bias jelas. Tunggu konfirmasi dari pergerakan harga di LTF.')
        lines.append('')
        lines.append('⚠️ Disclaimer: Analisis untuk edukasiのみ, bukan saran keuangan.')
        return chr(10).join(lines)

