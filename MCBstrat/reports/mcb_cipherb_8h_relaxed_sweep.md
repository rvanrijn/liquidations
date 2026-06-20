# MCB Cipher B 8H HA — Relaxed Entry (arming-window sweep)

**Date:** 2026-06-20 · Bybit BTCUSDT 8H Heikin-Ashi, 2023-05-22..2026-06-20 (3374 bars)
Costs 0.10% fees + 0.05% slippage/RT · signals on closed HA bar, fills next real open (no-lookahead).

## What changed

The original rule required a dot AND money-flow to agree on the **same** bar, which fired only 19
times in 3 years (self-starving). The relaxation: a dot **arms** a position for `W` bars; entry
fires on the first bar within `[dot, dot+W]` where money-flow confirms (long mf>0 / short mf<0).
`W=0` reproduces the original strict rule. Code: `mcbstrat/signals.py` (`build_signals(arm_window=W)`),
swept by `mcbstrat/run_sweep.py`.

## Full-period sweep (this is in-sample — do NOT read as an edge)

```
  W trades long short   win%    PF    exp%     net%    zero%  maxDD%
  0     19   12     7  21.1  0.20  -1.50   -25.4   -22.5  -28.9   (original strict)
  1     24   15     9  29.2  0.35  -1.07   -23.4   -19.6  -26.9
  2     25   16     9  28.0  0.56  -0.73   -18.0   -13.9  -28.8
  3     28   18    10  32.1  1.74   1.14    23.4    30.4  -18.2
  5     34   22    12  38.2  2.96   2.62    93.9   107.4  -18.2   (full-period peak)
  8     43   29    14  32.6  2.13   1.68    63.4    77.9  -25.8
 13     46   29    17  32.6  2.02   1.50    58.2    73.3  -26.6
```

A clean optimization curve peaking at W=5 — the classic shape of an **overfit single parameter**
(one asset, one period, ~34 trades, a sharp W2→W3 cliff from −18% to +23%). Selecting W=5 because it
wins over the whole period is look-ahead parameter selection.

## The honest test: tune W in-sample (first 70%), measure it out-of-sample

```
  W  IS_n  IS_exp%  IS_net% | OOS_n  OOS_exp%  OOS_net%
  0    13   -1.89   -22.2 |     6    -0.66     -4.1
  1    16   -1.41   -20.8 |     8    -0.38     -3.2
  2    17   -0.90   -15.3 |     8    -0.38     -3.2
  3    19    2.00    31.5 |     9    -0.68     -6.2
  5    23    4.24   111.6 |    11    -0.77     -8.4
  8    30    2.91    90.6 |    13    -1.15    -14.3
 13    32    2.64    85.3 |    14    -1.10    -14.6
```

**In-sample-best W = 5 → out-of-sample: n=11, −0.77%/trade, −8.4% net, win 27%, PF 0.41.**

Every window that is positive in-sample is **negative out-of-sample** — a perfect sign-flip across
the board. The relaxation's apparent edge exists only in the 2023–2024 fit window and inverts in
2025–2026.

## Verdict

**Relaxing the dot+MF co-occurrence does NOT recover a real edge — it manufactures an in-sample
mirage that fails out-of-sample at every window.** Combined with the earlier strict-rule result
(no edge) and the live cross-check finding (the open VuManChu clone diverges in oscillator scale
from the operator's proprietary MarketCipher B, so its dots fire at different times than real MCB),
the conclusion stands: **do not deploy; thread closed.** Same failure signature as the PVT→MF
precursor — genuine in-sample structure, zero out-of-sample survival.

The only path that could change the conclusion is testing against the **real** MCB dots (captured
forward via TV replay/study-values), since this backtest is faithful to the clone, not to MCB.
That is a separate, manual, forward-data project — not a parameter to tune here.
