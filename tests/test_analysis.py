"""Tests for proteus_bench.analysis: series, noise model, flags, steps and boundaries.

Contract clauses: metrics are derived from a record's timings (init-stage iterations
left out of per-iteration medians, components counted over the iterations they ran
in); points are ordered by start time; only comparable runs enter baselines; sigma is
1.4826 x MAD floored at the machine class's relative noise; a flag needs
|delta| > max(3 sigma, 5 %) and |delta| above the unit's absolute floor, and is
confirmed by the next comparable point; baselines, flags and steps restart at a
settings boundary, which lists the changed keys; with fewer than 3 prior comparable
points there is no baseline.

Histories come from the ``make_records`` and ``noisy_factors`` fixtures (conftest):
examples/record.json with every duration scaled per run, noise seeded with 42.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import pytest

from proteus_bench.analysis import (
    DEFAULT_NOISE_FLOOR_REL,
    MIN_DELTA_REL,
    analyse,
    baseline,
    record_metrics,
)
from proteus_bench.settings import settings_hash

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

EXAMPLE_RECORD = Path(__file__).parents[1] / 'examples' / 'record.json'
TOTAL_S = 2961.2  # wall_s of examples/record.json


def _series(result: dict, metric: str) -> dict:
    return next(s for s in result['series'] if s['key']['metric'] == metric)


def _n(run_id: str) -> int:
    """Run index i of a synthetic record (run_id ends in ``-run{i:03d}``)."""
    return int(run_id.rsplit('-run', 1)[1])


def _all_flags(result: dict) -> list[dict]:
    return [f for s in result['series'] for f in s['flags']]


def _all_steps(result: dict) -> list[dict]:
    return [st for s in result['series'] for st in s['steps']]


def test_record_metrics_from_the_example_record():
    """Phase, per-iteration, component and submodule metrics match hand sums."""
    record = json.loads(EXAMPLE_RECORD.read_text())
    metrics = record_metrics(record)
    assert metrics['total'] == pytest.approx(2961.2)
    assert metrics['init'] == pytest.approx(1772.0)
    assert metrics['n_iters'] == 6
    assert 'setup' not in metrics  # phase is null in the record
    # Iterations 2..6 take 19.7, 159.7, 19.7, 19.7, 159.7 s: median 19.7 s. Keeping the
    # init-stage iteration 1 (784.7 s) would move the median to (19.7 + 159.7) / 2.
    assert metrics['loop_per_iter_median'] == pytest.approx(19.7)
    assert abs(metrics['loop_per_iter_median'] - 89.7) > 1
    # Structure re-solves run in iterations 3 and 6 (140 s each). Counting the other
    # steady iterations as 0 s would give a median of 0 s.
    assert metrics['loop.structure.per_iter_median'] == pytest.approx(140.0)
    assert metrics['loop.atmos.per_iter_median'] == pytest.approx(15.0)
    assert metrics['init.structure'] == pytest.approx(1768.0)
    assert metrics['init.other'] == pytest.approx(0.0, abs=1e-12)
    # Zalmoxis: 1768 s in init plus 280 s of loop re-solves; init alone would be 1768 s.
    assert metrics['submodule.zalmoxis.total'] == pytest.approx(2048.0)
    assert metrics['submodule.calliope.total'] == pytest.approx(8.0)
    assert not any(name.startswith('submodule.None') for name in metrics)


def test_all_init_stage_iterations_give_no_loop_medians_and_missing_fields_raise():
    """Without steady iterations there is nothing to take a median of; bad records fail loudly."""
    record = json.loads(EXAMPLE_RECORD.read_text())
    for row in record['timings']['per_iter']:
        row['init_stage'] = True
    metrics = record_metrics(record)
    assert 'loop_per_iter_median' not in metrics
    assert not [name for name in metrics if name.startswith('loop.')]
    assert metrics['loop'] == pytest.approx(1163.2)
    del record['timings']
    with pytest.raises(
        ValueError, match=r"record '20260925T031000Z-habrok-default-a1b2'.*timings"
    ):
        analyse([record])


def test_stable_noisy_history_raises_no_flags_or_steps(make_records, noisy_factors):
    """50 runs with 1.5 % Gaussian noise: nothing is flagged and no step is found."""
    factors = noisy_factors(50)
    # The noise is really there: sample scatter within 1.0-2.0 % of the level.
    assert 0.010 < statistics.stdev(factors) < 0.020
    result = analyse(make_records(factors))
    assert _all_flags(result) == []
    assert _all_steps(result) == []
    total = _series(result, 'total')
    assert len(total['points']) == 50
    assert total['baseline']['n'] == 10
    assert total['baseline']['sigma'] >= DEFAULT_NOISE_FLOOR_REL * total['baseline']['median']
    assert total['unit'] == 's' and _series(result, 'n_iters')['unit'] == 'count'


def test_false_positive_rate_on_stable_noise(make_records, noisy_factors):
    """Over 10 seeds of 50 runs at 1.5 % noise, under 0.5 % of judged runs flag, none confirmed.

    Seeds 0-9 give 1 flag in 470 judged runs (0.21 %, seed 2, run 32, -5.3 %); a longer
    offline check over 2000 seeds gave 169 in 94000 (0.18 %), 2 of them confirmed.
    """
    flags, judged = [], 0
    for seed in range(10):
        records = make_records(noisy_factors(50, seed=seed))
        for record in records:  # keep only the total series, for speed
            record['timings'].update(phases={}, components=[], per_iter=[])
            record['outcome']['n_iters'] = None
        flags += _series(analyse(records), 'total')['flags']
        judged += 50 - 3  # the first 3 runs have no baseline
    assert len(flags) / judged < 0.005
    assert len(flags) == 1
    assert not any(f['confirmed'] for f in flags)
    assert all(abs(f['delta_rel']) > MIN_DELTA_REL for f in flags)


@pytest.mark.parametrize(('step_rel', 'kind'), [(0.10, 'regression'), (-0.10, 'improvement')])
def test_ten_percent_step_is_flagged_confirmed_and_detected(
    make_records, noisy_factors, step_rel, kind
):
    """A 10 % level change at run 30 flags there, is confirmed, and is found as a step."""
    result = analyse(make_records(noisy_factors(50, step_at=30, step_rel=step_rel)))
    total = _series(result, 'total')
    first = total['flags'][0]
    assert _n(first['run_id']) == 30
    assert first['kind'] == kind
    assert first['confirmed'] is True
    assert first['delta_rel'] == pytest.approx(step_rel, abs=0.03)
    # MAD-based 3 sigma of this history is below 5 %, so the 5 % rule sets the threshold.
    assert first['threshold_rel'] == pytest.approx(MIN_DELTA_REL, rel=1e-9)
    # Once 6 of the 10 baseline runs are past the step the median has moved: no flags
    # after run 35.
    assert max(_n(f['run_id']) for f in total['flags']) <= 35
    assert [_n(st['after_run_id']) for st in total['steps']] == [30]
    step = total['steps'][0]
    assert step['delta_rel'] == pytest.approx(step_rel, abs=0.02)
    assert step['before'] == pytest.approx(TOTAL_S, rel=0.02)


def test_three_percent_step_is_below_the_five_percent_rule(make_records, noisy_factors):
    """A 3 % step is not flagged, and asv's detector does not find it in this history.

    3 % is two noise standard deviations. An offline check over 200 seeds found this
    step at run 30 in 22 of them, so missing it here is typical, not a bug.
    """
    factors = noisy_factors(50, step_at=30, step_rel=0.03)
    # The step is in the data: mean after / mean before is 1.03 within noise.
    assert statistics.mean(factors[30:]) / statistics.mean(factors[:30]) == pytest.approx(
        1.03, abs=0.01
    )
    total = _series(analyse(make_records(factors)), 'total')
    assert total['flags'] == []
    assert total['steps'] == []
    assert total['baseline']['median'] == pytest.approx(1.03 * TOTAL_S, rel=0.015)


def test_sub_second_component_jump_is_not_flagged(make_records, noisy_factors):
    """0.2 s -> 0.45 s per iteration is +125 % but below the 1 s floor; 15 s -> 33.75 s flags."""

    def jump(i, record):
        scale = 2.25 if i >= 30 else 1.0
        for row in record['timings']['per_iter']:
            row['components']['outgas'] = 0.2 * scale
            row['components']['atmos'] *= scale

    result = analyse(make_records(noisy_factors(50), edit=jump))
    outgas = _series(result, 'loop.outgas.per_iter_median')
    assert outgas['flags'] == []
    # Only the absolute floor stops it: +125 % is far above the 5 % rule.
    jump_rel = outgas['points'][30]['value'] / outgas['points'][29]['value'] - 1
    assert jump_rel == pytest.approx(1.25) and jump_rel > 20 * MIN_DELTA_REL
    # The step detector has no floor, so the change stays findable in the history.
    assert [_n(st['after_run_id']) for st in outgas['steps']] == [30]
    atmos = _series(result, 'loop.atmos.per_iter_median')
    assert _n(atmos['flags'][0]['run_id']) == 30
    assert atmos['flags'][0]['delta_abs'] > 1.0


def test_non_comparable_runs_stay_out_of_baselines(make_records, noisy_factors):
    """Five slow runs marked not comparable are shown but change no baseline, flag or step."""

    def radau_fallback(i, record):
        if 10 <= i < 15:
            record['comparability'] = {'ok': False, 'reasons': ['aragog.solver=radau']}
            record['timings']['wall_s'] *= 1.5

    total = _series(analyse(make_records(noisy_factors(30), edit=radau_fallback)), 'total')
    slow = [p for p in total['points'] if not p['comparable']]
    assert [_n(p['run_id']) for p in slow] == [10, 11, 12, 13, 14]
    assert min(p['value'] for p in slow) > 1.4 * TOTAL_S
    # Had they entered the baseline, run 15 would sit below a median near 1.25 x level.
    assert total['flags'] == []
    assert total['steps'] == []
    assert total['baseline']['n'] == 10
    assert total['baseline']['median'] == pytest.approx(TOTAL_S, rel=0.02)


def test_settings_change_is_a_boundary_that_restarts_the_baseline(make_records, noisy_factors):
    """Switching Zalmoxis to JAX at run 25 (30 % faster) ends comparison with older runs."""

    def switch_to_jax(i, record):
        if i < 25:
            return
        settings = record['benchmark']['settings']
        settings['interior_struct.zalmoxis.use_jax'] = True
        settings['params.out.path'] = f'run{i}'  # per-run key: not a settings change
        record['benchmark']['settings_hash'] = settings_hash(settings)
        record['timings']['wall_s'] *= 0.7 * (1.3 if i == 27 else 1.0)

    result = analyse(make_records(noisy_factors(30), edit=switch_to_jax))
    total = _series(result, 'total')
    expected = [
        {
            'run_id': '20260126T031000Z-run025',
            'reason': 'settings_changed',
            'changed_keys': ['interior_struct.zalmoxis.use_jax'],
        }
    ]
    assert total['boundaries'] == expected
    assert _series(result, 'n_iters')['boundaries'] == expected
    # Without the reset run 25 would flag as a 30 % improvement and a step would show.
    # Run 27 (+30 % on the new level) has only 2 prior runs in its segment: no baseline.
    # The detector splits the 5-run segment into one-run levels; none is a step.
    assert total['flags'] == []
    assert total['steps'] == []
    assert total['baseline']['n'] == 5
    assert total['baseline']['median'] == pytest.approx(0.7 * TOTAL_S, rel=0.03)


def test_too_few_points_give_no_baseline_and_points_follow_time(make_records):
    """Two comparable runs give a null baseline, three give one; order is by start time."""
    assert analyse([]) == {'schema': 'proteus-bench-analysis/1', 'series': []}
    two = _series(analyse(make_records([1.0, 1.2])), 'total')
    assert two['baseline'] is None
    assert two['flags'] == [] and two['steps'] == []
    records = make_records([1.0, 1.0, 1.3])
    three = _series(analyse(records[::-1]), 'total')
    assert [_n(p['run_id']) for p in three['points']] == [0, 1, 2]
    assert three['points'][0]['commit'] == '00000000'
    assert three['baseline']['n'] == 3
    # Run 2 has only 2 prior runs, so even a 30 % jump is not judged.
    assert three['flags'] == []


def _pinned_prior() -> list[float]:
    # Median 1.00; absolute deviations 0, .02, .02, .04, .04, .01, .01, .03, .03, 0,
    # so MAD = 0.02 and sigma = 1.4826 x 0.02 = 0.029652; 3 sigma = 0.088956 > 5 %.
    return [1.00, 1.02, 0.98, 1.04, 0.96, 1.01, 0.99, 1.03, 0.97, 1.00]


def test_threshold_uses_scaled_mad_not_standard_deviation(make_records):
    """+8 % stays inside 3 x 1.4826 x MAD = 8.8956 %; +9.5 % flags with that threshold."""
    prior = _pinned_prior()
    base = baseline([TOTAL_S * f for f in prior], DEFAULT_NOISE_FLOOR_REL)
    assert base['sigma'] / TOTAL_S == pytest.approx(0.029652, rel=1e-9)
    # Guard: the sample standard deviation (0.025820) or the raw MAD (0.02) would give
    # a 3 sigma threshold of 7.75 % or 6 %, both below the +8 % point below.
    assert 3 * statistics.stdev(prior) == pytest.approx(0.077460, rel=1e-4)
    assert 3 * statistics.stdev(prior) < 0.08 and 3 * 0.02 < 0.08
    inside = _series(analyse(make_records([*prior, 1.08])), 'total')
    assert inside['flags'] == []
    outside = _series(analyse(make_records([*prior, 1.095])), 'total')
    (flag,) = outside['flags']
    assert flag['threshold_rel'] == pytest.approx(0.088956, rel=1e-9)
    assert flag['delta_rel'] == pytest.approx(0.095, rel=1e-9)
    assert flag['delta_abs'] == pytest.approx(0.095 * TOTAL_S, rel=1e-9)
    assert flag['confirmed'] is False  # no later run to confirm it


def test_noise_floor_applies_per_machine_class(make_records):
    """With zero scatter, sigma comes from the class floor: 3 % floor hides a +7 % run."""
    records = make_records([1.0] * 10 + [1.07])
    default = _series(analyse(records), 'total')
    (flag,) = default['flags']
    # 3 x 1.5 % = 4.5 % is below the 5 % rule, so the 5 % rule sets the threshold.
    assert flag['threshold_rel'] == pytest.approx(MIN_DELTA_REL, rel=1e-9)
    noisy_class = _series(analyse(records, noise_floor_rel={'habrok-vink': 0.03}), 'total')
    assert noisy_class['flags'] == []
    assert noisy_class['baseline']['sigma'] == pytest.approx(0.03 * TOTAL_S, rel=1e-9)
    other_class = _series(analyse(records, noise_floor_rel={'gha-ubuntu': 0.03}), 'total')
    assert len(other_class['flags']) == 1


def test_single_spike_flags_unconfirmed(make_records, noisy_factors):
    """One +10 % run followed by normal runs flags once and is not confirmed."""
    factors = noisy_factors(20)
    factors[12] *= 1.10
    total = _series(analyse(make_records(factors)), 'total')
    assert [_n(f['run_id']) for f in total['flags']] == [12]
    assert total['flags'][0]['confirmed'] is False
    assert total['flags'][0]['kind'] == 'regression'
    assert total['steps'] == []


def test_two_run_excursion_flags_but_is_not_a_step(make_records, noisy_factors):
    """Two +20 % runs flag (the first confirmed by the second) but form no reported step.

    The detector fits them as their own two-run level; steps need 3 runs on each side.
    """
    factors = noisy_factors(20)
    factors[12] *= 1.20
    factors[13] *= 1.20
    total = _series(analyse(make_records(factors)), 'total')
    assert [_n(f['run_id']) for f in total['flags']] == [12, 13]
    assert total['flags'][0]['confirmed'] is True
    assert total['flags'][0]['delta_rel'] == pytest.approx(0.20, abs=0.04)
    assert total['steps'] == []


def test_count_metric_and_zero_baseline(make_records):
    """One more iteration flags (floor 0 for counts); 0 s -> 5 s flags with no relative delta."""

    def change(i, record):
        if i == 10:
            record['outcome']['n_iters'] = 7
            rows = record['timings']['components']
            other = next(r for r in rows if (r['phase'], r['component']) == ('init', 'other'))
            other['total_s'] = 5.0

    result = analyse(make_records([1.0] * 11, edit=change))
    (n_iters_flag,) = _series(result, 'n_iters')['flags']
    # delta 1 > floor 0; with the 1 s floor of time metrics, 1 > 1 would not flag.
    assert n_iters_flag['delta_abs'] == 1
    assert n_iters_flag['delta_rel'] == pytest.approx(1 / 6)
    (other_flag,) = _series(result, 'init.other')['flags']
    assert other_flag['delta_abs'] == pytest.approx(5.0)
    assert other_flag['delta_rel'] is None and other_flag['threshold_rel'] is None
