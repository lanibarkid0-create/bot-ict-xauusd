"""Verifikasi offline mesin HTF->LTF (tanpa jaringan)."""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import gold_mcp_server as g

print("KONSTANTA:", ", ".join(n for n in dir(g) if n.isupper()))

n = 80
o = [float(3700 + i * 0.6 + (i % 7) - 3) for i in range(n)]
h = [x + 2.5 for x in o]
l = [x - 2.5 for x in o]
c = [x + 1.0 for x in o]
d = g._displacement(o, c, h, l)
bias, skor_b = g._smc_bias_from_series(c, o, h, l, d, None, "NAIK")
f = g._ltf_features(o, h, l, c)
sk, mx, ct = g._score_ltf(bias, f)
poi = g._pick_best_poi(bias, f["ob_bull"], f["ob_bear"], f["fvg_up"], f["fvg_dn"],
                       f["eq_h"], f["eq_l"], c[-1], l, h, f["atr"])
for mode, htf_lbl, ltf_lbl, sl, tp1, tp2, vh in (
    ("intraday", "H1", "M15", 80, 160, 240, 12),
    ("swing", "D1", "H1", 200, 400, 600, 72),
):
    r = g._build_htf_ltf_result(
        mode=mode, htf_label=htf_lbl, ltf_label=ltf_lbl, spot=c[-1],
        bias=bias, bias_skor=skor_b, f=f, skor=sk, maxi=mx, catatan=ct,
        poi=poi, sl_p=sl, tp1_p=tp1, tp2_p=tp2, min_score=4, valid_h=vh, pip=1.0,
        ll=l, lh=h, ltf_bar="uji-offline", disp_h=d, struct_h=None, htf_trend="NAIK",
    )
    assert r["zona_poi"]["bawah"] <= r["zona_poi"]["atas"], "zona invalid"
    assert r["risk_reward_tp1"] == "1:2.00" and r["risk_reward_tp2"] == "1:3.00", "RR rusak"
    html = g._format_htf_ltf_html(r, "UJI", "X")
    assert "Zona POI" in html and "Skor:" in html, "html kurang"
    print(f"OK {mode}: bias={bias} skor={r['probability_score']} arah={r['direction']} "
          f"poi={r['zona_poi']['jenis']} {r['zona_poi']['bawah']}-{r['zona_poi']['atas']}")

# Kasus TUNGGU: bias NETRAL -> tidak boleh menghasilkan entry.
poi2 = dict(poi, skor_zona=0, bawah=0.0, atas=0.0)
r2 = g._build_htf_ltf_result(
    mode="intraday", htf_label="H1", ltf_label="M15", spot=c[-1], bias="NETRAL",
    bias_skor={"BUY": 1, "SELL": 1}, f=f, skor=1, maxi=6, catatan=[], poi=poi2,
    sl_p=80, tp1_p=160, tp2_p=240, min_score=4, valid_h=12, pip=1.0,
    ll=l, lh=h, ltf_bar="uji", disp_h=d, struct_h=None, htf_trend="RATA",
)
assert r2["direction"] == "TUNGGU" and r2["order_type"] == "WAIT", "harusnya TUNGGU"
print("OK tunggu: " + r2["reason"][:80])
print("SEMUA VERIFIKASI OFFLINE LULUS")

