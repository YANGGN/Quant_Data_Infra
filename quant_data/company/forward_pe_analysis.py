"""Trailing descriptive statistics over saved P/E; never alter source values."""
from bisect import bisect_left, insort
from collections import deque
from math import fsum, isfinite, sqrt

from ..errors import ResourceLimitError, ValidationError
from .forward_pe_reader import read_forward_pe

MODEL_VERSION = "forward_pe_rolling.v1"
MAX_OUTPUT_DAYS = 30000


def _quantile(ordered, probability):
    position = (len(ordered) - 1) * probability
    lower = int(position)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[min(lower + 1, len(ordered) - 1)] * weight


def _moments(values, current):
    # Scaling avoids overflow and centering avoids subtracting two large moments.
    scale = max((abs(x) for x in values), default=0) or 1
    normalized = [x / scale for x in values]
    mean = fsum(normalized) / len(normalized)
    deviation = sqrt(fsum((x - mean) ** 2 for x in normalized) / len(normalized))
    score = (current / scale - mean) / deviation if deviation else None
    return mean * scale, deviation * scale, score


def rolling_statistics(days, *, window_sessions=756, min_observations=252,
                       winsor_tail_pct=2.5, checkpoint=lambda: None):
    """One trailing window per saved session, including that session.

    Nulls occupy sessions but are excluded from moments/quantiles. Type-7 linear
    quantiles cap BOTH the current P/E and the sample before population moments.
    Negative P/E remains in the signed sample; this is descriptive, not a rank
    of investment attractiveness. No future sample or full-range fit is used.
    """
    if not 2 <= min_observations <= window_sessions <= 1260:
        raise ValidationError("Invalid rolling window or minimum observations")
    if winsor_tail_pct not in (0, 1, 2.5, 5):
        raise ValidationError("Unsupported winsorization tail")
    trailing, ordered, output = deque(), [], []
    previous = None
    for index, day in enumerate(days):
        if index % 64 == 0:
            checkpoint()
        if previous is not None and day[0] <= previous:
            raise ValidationError("Rolling observations must have unique increasing dates")
        previous = day[0]
        value = day[3]
        if value is not None and (isinstance(value, bool) or not isfinite(value)):
            raise ValidationError("Saved P/E must be finite or missing")
        trailing.append((day[0], value))
        if value is not None:
            insort(ordered, value)
        if len(trailing) > window_sessions:
            _, removed = trailing.popleft()
            if removed is not None:
                ordered.pop(bisect_left(ordered, removed))
        row = dict(trade_date=day[0], raw_pe=value, winsorized_pe=None,
                   raw_z_score=None, z_score=None, z_score_reason=None,
                   raw_z_score_reason=None, rolling_mean=None, rolling_std=None,
                   raw_rolling_mean=None, raw_rolling_std=None,
                   lower_bound=None, upper_bound=None, was_clipped=False,
                   valid_count=len(ordered), session_count=len(trailing),
                   clipped_count=0, negative_count=bisect_left(ordered, 0),
                   window_start=trailing[0][0], window_end=day[0])
        reason = "missing_pe" if value is None else "insufficient_history" if len(ordered) < min_observations else None
        if reason:
            row.update(z_score_reason=reason, raw_z_score_reason=reason)
        else:
            raw_mean, raw_std, raw_z = _moments(ordered, value)
            tail = winsor_tail_pct / 100
            low, high = (_quantile(ordered, tail), _quantile(ordered, 1 - tail)) if tail else (ordered[0], ordered[-1])
            sample = [min(high, max(low, x)) for x in ordered]
            capped = min(high, max(low, value))
            mean, std, score = _moments(sample, capped)
            row.update(winsorized_pe=capped, raw_z_score=raw_z, z_score=score,
                       rolling_mean=mean, rolling_std=std,
                       raw_rolling_mean=raw_mean, raw_rolling_std=raw_std,
                       lower_bound=low if tail else None, upper_bound=high if tail else None,
                       was_clipped=capped != value,
                       clipped_count=sum(x < low or x > high for x in ordered),
                       z_score_reason="zero_variance" if score is None else None,
                       raw_z_score_reason="zero_variance" if raw_z is None else None)
        output.append(row)
    return output


def read_analysis(root, ticker, period, *, window_sessions=756,
                  min_observations=252, winsor_tail_pct=2.5, checkpoint=lambda: None):
    result = read_forward_pe(root, ticker, period, history_sessions=window_sessions - 1)
    if len(result["days"]) > MAX_OUTPUT_DAYS:
        raise ResourceLimitError("Forward P/E analysis accepts at most 30,000 saved sessions; select a shorter history")
    history = result.pop("history", [])
    statistics = rolling_statistics(history + result["days"], window_sessions=window_sessions,
                                    min_observations=min_observations,
                                    winsor_tail_pct=winsor_tail_pct, checkpoint=checkpoint)
    result["statistics"] = statistics[len(history):]
    result["analysis"] = dict(model_version=MODEL_VERSION, window_sessions=window_sessions,
                              min_observations=min_observations, winsor_tail_pct=winsor_tail_pct,
                              warmup_sessions=len(history), includes_current=True,
                              variance_ddof=0, quantile_method="linear_type_7")
    return result
