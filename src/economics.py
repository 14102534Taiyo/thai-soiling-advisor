"""Economics/Optimization module.

Day-indexing convention: `soiling_ratio` and `expected_generation` are
treated as ordered daily series where the FIRST element (position 0) is
day 1 -- the first full day after cleaning (some soiling has already
accumulated by the end of that day). We read them positionally
(`.iloc`), not by label, so this works regardless of whether the caller's
pandas index is a 0-based RangeIndex, a 1-based day count, or an actual
date index -- only the ordering and length matter. Day 0 (the instant of
cleaning, ratio == 1.0) is not part of these series and is never summed,
since a full day of clean-panel generation is never actually lost on the
cleaning day itself.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd


def compute_acr_curve(soiling_ratio: pd.Series, expected_generation: pd.Series,
                       price_per_kwh: float, cleaning_cost_per_kwp: float,
                       kwp: float, max_days: int = 60) -> pd.DataFrame:
    if max_days < 1:
        raise ValueError(f"max_days must be >= 1, got {max_days}")
    if len(soiling_ratio) == 0 or len(expected_generation) == 0:
        raise ValueError("soiling_ratio and expected_generation must not be empty")

    n = min(max_days, len(soiling_ratio), len(expected_generation))

    soiling_vals = soiling_ratio.to_numpy()[:n]
    generation_vals = expected_generation.to_numpy()[:n]

    daily_loss_value = (1.0 - soiling_vals) * generation_vals * price_per_kwh
    cumulative_loss = np.cumsum(daily_loss_value)

    t = np.arange(1, n + 1)
    cleaning_cost = cleaning_cost_per_kwp * kwp
    loss_cost_per_day = cumulative_loss / t
    clean_cost_per_day = cleaning_cost / t
    acr = loss_cost_per_day + clean_cost_per_day

    return pd.DataFrame({
        "T": t.astype(int),
        "acr": acr,
        "loss_cost_per_day": loss_cost_per_day,
        "clean_cost_per_day": clean_cost_per_day,
    })


def _contiguous_band(acr_curve: pd.DataFrame, optimal_T: int, tolerance: float = 0.05) -> tuple[int, int]:
    """Widest contiguous run of T around optimal_T whose acr stays within
    `tolerance` fraction of acr[optimal_T] -- expands outward one T at a
    time and stops the moment either side exceeds tolerance, rather than
    taking min/max T over ALL points within tolerance globally. That
    naive global version breaks silently on a non-convex ACR curve (see
    recommend_cleaning's docstring): with two near-tied but separated
    local minima, it would span the whole gap between them -- including
    the expensive hump in between, which is NOT actually a good day to
    clean -- and report it as one wide "recommended range." Expanding
    outward from optimal_T only stops at that hump, so it correctly
    reports just the one basin optimal_T actually sits in.
    """
    acr_by_T = acr_curve.set_index("T")["acr"]
    threshold = acr_by_T.loc[optimal_T] * (1 + tolerance)
    t_min, t_max = int(acr_curve["T"].min()), int(acr_curve["T"].max())

    lo = optimal_T
    while lo - 1 >= t_min and acr_by_T.loc[lo - 1] <= threshold:
        lo -= 1
    hi = optimal_T
    while hi + 1 <= t_max and acr_by_T.loc[hi + 1] <= threshold:
        hi += 1
    return lo, hi


def recommend_cleaning(acr_curve: pd.DataFrame, last_clean_date, min_T: int = 1, band_tolerance: float = 0.05) -> dict:
    """min_T is the earliest T still actionable "today" -- normally
    max(1, days already elapsed since last_clean_date), passed by the
    caller (it isn't derivable from acr_curve alone, which carries no
    notion of "today"). ACR is typically U-shaped (loss_cost_per_day
    decreases, clean_cost_per_day increases) when the underlying soiling
    trajectory is smooth, but is NOT always: representative-year replay
    (src/representative_year.py) stitches a DIFFERENT real source year
    per calendar month, so the cumulative-loss curve can kink at each
    month boundary and produce a genuinely multi-modal (wiggly) ACR curve
    with more than one local minimum -- confirmed live 2026-08-15: two
    minima 22 days apart differing by just 0.5% in cost, where a 1-day
    shift in "today" (new real data replacing one fill day) flipped which
    one was the global minimum, making the single-point recommended_date
    jump by three weeks between reruns despite the actual cost difference
    being negligible. band_low_T/band_high_T (see _contiguous_band)
    report the basin optimal_T actually sits in, in T units, so a caller
    can show "any day in this range is about equally good" instead of a
    single date that may look unstable across reruns for a reason that
    has nothing to do with the model being wrong.

    Also: the unconstrained global minimum can sit at a T that has
    ALREADY PASSED relative to today if the true optimum came and went
    while more data accumulated (e.g. a wider stretch became visible
    after a rerun) -- naively reporting that stale T produces a
    "recommended date" in the past, which happened for real in a
    same-day rerun backtest (2026-01-19 -> 01-20: recommended date jumped
    from a future day to 8 days in the past). Restricting the search to
    T >= min_T fixes it: since ACR increases monotonically past ITS
    OWN local minimum, if that minimum is behind us the best still
    -reachable choice within this basin is the earliest one left, min_T
    itself (clean today) -- not a re-derivation of the stale
    already-passed optimum.
    """
    candidates = acr_curve[acr_curve["T"] >= min_T]
    if candidates.empty:
        candidates = acr_curve
    best_row = candidates.loc[candidates["acr"].idxmin()]
    optimal_T = int(best_row["T"])
    acr_at_optimal = float(best_row["acr"])
    band_low_T, band_high_T = _contiguous_band(acr_curve, optimal_T, tolerance=band_tolerance)

    if isinstance(last_clean_date, date):
        base_date = last_clean_date
    else:
        base_date = pd.Timestamp(last_clean_date).date()
    recommended_date = base_date + timedelta(days=optimal_T)
    band_low_date = base_date + timedelta(days=band_low_T)
    band_high_date = base_date + timedelta(days=band_high_T)

    acr_at_t1 = float(acr_curve.loc[acr_curve["T"] == acr_curve["T"].min(), "acr"].iloc[0])
    acr_at_tmax = float(acr_curve.loc[acr_curve["T"] == acr_curve["T"].max(), "acr"].iloc[0])

    return {
        "optimal_T": optimal_T,
        "recommended_date": recommended_date,
        "band_low_T": band_low_T,
        "band_high_T": band_high_T,
        "band_low_date": band_low_date,
        "band_high_date": band_high_date,
        "acr_at_optimal": acr_at_optimal,
        "savings_vs_daily_clean": acr_at_t1 - acr_at_optimal,
        "savings_vs_never_clean": acr_at_tmax - acr_at_optimal,
    }


if __name__ == "__main__":
    # 0.2%/day soiling loss, 4 THB/kWh, and 15 THB/kWp cleaning cost on a
    # 100 kWp array put the true break-even around T~130-140 days (cleaning
    # cost term ~1500 THB is cheap relative to how slowly value is lost).
    # A 45-day-only demo would never see the curve turn over and the
    # sanity check below would trip on the "never wants to clean" edge --
    # so we extend the synthetic horizon (still the same ~0.2%/day linear
    # decay, no rain reset, per the assumption) far enough to show the
    # actual interior optimum, rather than shrinking cleaning cost to
    # force an artificially early break-even.
    n_days = 200
    day = np.arange(1, n_days + 1)
    soiling_ratio = pd.Series(np.clip(1.0 - 0.002 * day, 0.0, 1.0))
    expected_generation = pd.Series(np.full(n_days, 20.0))

    price_per_kwh = 4.0  # THB/kWh, plausible Thai residential/avoided-cost retail rate
    cleaning_cost_per_kwp = 15.0  # THB/kWp per wash -- placeholder order-of-magnitude assumption
    kwp = 100.0

    acr_curve = compute_acr_curve(
        soiling_ratio, expected_generation,
        price_per_kwh, cleaning_cost_per_kwp, kwp,
        max_days=180,
    )

    result = recommend_cleaning(acr_curve, last_clean_date=date(2026, 8, 8))

    min_idx = acr_curve["acr"].idxmin()
    lo = max(0, min_idx - 3)
    hi = min(len(acr_curve), min_idx + 4)
    print("ACR curve minimum region:")
    print(acr_curve.iloc[lo:hi].to_string(index=False))
    print()
    print("recommend_cleaning output:")
    for k, v in result.items():
        print(f"  {k}: {v}")

    optimal_T = result["optimal_T"]
    assert optimal_T != 1, "optimal_T == 1: cleaning cost dominates, daily cleaning looks optimal -- check inputs"
    assert optimal_T != len(acr_curve), "optimal_T == max_days: model never wants to clean -- check inputs"
    print()
    print(f"Sanity check passed: optimal_T={optimal_T} is a sensible middle value "
          f"(neither 1 nor {len(acr_curve)}).")
