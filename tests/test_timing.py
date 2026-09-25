"""Tests for proteus_bench.timing: reading timing.jsonl and the span tree rules.

Contract clauses exercised: phases are disjoint roots; children lie inside their
parents; at most one attributed span per root-to-leaf path and no overlap between
attributed spans, so that attributed time plus 'other' equals each phase total;
crashed runs (no run_end) stay readable; a torn final line is dropped.
"""

from __future__ import annotations

import copy
import json

import pytest

from proteus_bench.testing.fake_proteus import FAKE_DEFAULTS
from proteus_bench.timing import TOLERANCE_S, attributed_totals, check_events, read_events

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def _span(events, name, nth=0):
    """The nth span with a given name, in file order."""
    return [e for e in events if e['ev'] == 'span' and e['name'] == name][nth]


def test_default_fake_run_is_consistent_and_has_expected_layout(good_events):
    """A normal run passes every tree rule and has one span per iteration."""
    assert check_events(good_events) == []
    roots = [e['name'] for e in good_events if e['ev'] == 'span' and e['parent'] is None]
    assert roots == ['init', 'loop', 'shutdown']
    iters = [e['iter'] for e in good_events if e['ev'] == 'span' and e['name'] == 'iter']
    assert iters == [1, 2, 3, 4]
    assert good_events[-1]['status'] == 'ok'


def test_attributed_time_plus_other_equals_each_phase_total(good_events):
    """Attributed spans and 'other' add up to the phase, with pinned structure totals."""
    totals = attributed_totals(good_events)
    for phase in totals.values():
        attributed = sum(s for s, _ in phase['attributed'].values())
        assert attributed + phase['other'] == pytest.approx(phase['total'], abs=1e-9)
        assert phase['other'] >= -TOLERANCE_S
    init_structure = totals['init']['attributed'][('structure', 'zalmoxis', 'numpy')]
    # First solve plus two equilibration solves at 420/k s: 1138 + 420 + 210 = 1768 s.
    assert init_structure[0] == pytest.approx(1768.0, rel=1e-9)
    assert init_structure[1] == 3
    # Guard: dropping the equilibration children would give 1138 s, double counting
    # the equilibrate parent would give 2398 s; both are far outside the tolerance.
    assert abs(init_structure[0] - 1138.0) > 100 and abs(init_structure[0] - 2398.0) > 100
    loop = totals['loop']['attributed']
    # Re-solve every 3rd iteration: exactly one 140 s JAX solve in 4 iterations.
    assert loop[('structure', 'zalmoxis', 'jax')] == [pytest.approx(140.0), 1]
    # First AGNI solve 780 s, then 15 s each: 780 + 3 * 15 = 825 s.
    assert loop[('atmos', 'agni', None)][0] == pytest.approx(825.0)


def test_component_on_a_span_and_its_ancestor_is_rejected(good_events):
    """Tagging the equilibrate group as well as its children would double count."""
    events = copy.deepcopy(good_events)
    _span(events, 'equilibrate')['component'] = 'structure'
    problems = check_events(events)
    assert any('ancestor' in p for p in problems)
    # The same events without the extra tag are fine, so the rule caused the failure.
    assert check_events(good_events) == []


def test_child_extending_past_its_parent_is_rejected_beyond_tolerance(good_events):
    """Containment allows rounding slack of TOLERANCE_S, not more."""
    events = copy.deepcopy(good_events)
    shutdown = _span(events, 'shutdown')
    loop = _span(events, 'loop')
    last_iter = [e for e in events if e['ev'] == 'span' and e['name'] == 'iter'][-1]
    # Edge case: ending half a tolerance past the parent end still passes.
    last_iter['dur'] = loop['t0'] + loop['dur'] - last_iter['t0'] + 0.5 * TOLERANCE_S
    assert check_events(events) == []
    last_iter['dur'] += 2 * TOLERANCE_S
    problems = check_events(events)
    assert any('outside parent' in p for p in problems)
    assert shutdown['t0'] > loop['t0']  # the edit did not move the phases themselves


def test_overlapping_attributed_siblings_are_rejected(good_events):
    """Two attributed spans running at the same time cannot both count."""
    events = copy.deepcopy(good_events)
    first = _span(events, 'outgas')
    second = _span(events, 'structure', nth=1)
    second['t0'] = first['t0']
    problems = check_events(events)
    assert any('overlap in time' in p for p in problems)
    assert len(problems) >= 1


def test_missing_parent_is_tolerated_only_for_crashed_runs(good_events):
    """Without run_end, spans left open by a crash may be absent; otherwise not."""
    loop_id = _span(good_events, 'loop')['id']
    without_loop = [e for e in good_events if not (e['ev'] == 'span' and e['id'] == loop_id)]
    finished = check_events(without_loop)
    assert any(f'parent {loop_id} does not exist' in p for p in finished)
    crashed = check_events(without_loop[:-1])  # also drop run_end
    assert crashed == []


def test_envelope_errors_are_reported_before_tree_checks(good_events):
    """Empty input, wrong version, unknown kinds and misplaced run_start are caught."""
    assert check_events([]) == ['no events']
    bumped = copy.deepcopy(good_events)
    bumped[3]['v'] = 2
    assert check_events(bumped) == ['event 3: unsupported version 2']
    unknown = copy.deepcopy(good_events) + [{'v': 1, 'ev': 'heartbeat'}]
    assert any('unknown event kind' in p for p in check_events(unknown))
    swapped = [good_events[1], good_events[0], *good_events[2:]]
    assert any('run_start must be the first' in p for p in check_events(swapped))
    truncated = copy.deepcopy(good_events)
    del truncated[2]['dur']
    assert any('missing dur' in p for p in check_events(truncated))


def test_iteration_rules(good_events):
    """Iteration numbers must increase and the iter field belongs on iter spans only."""
    events = copy.deepcopy(good_events)
    iters = [e for e in events if e['ev'] == 'span' and e['name'] == 'iter']
    iters[2]['iter'] = iters[1]['iter']  # repeated number
    assert any('not increasing' in p for p in check_events(events))
    events = copy.deepcopy(good_events)
    _span(events, 'star')['iter'] = 0
    assert any('iter field belongs' in p for p in check_events(events))


def test_phase_layout_rules(good_events):
    """Phases may touch but not overlap, must be known names, and appear once each."""
    events = copy.deepcopy(good_events)
    loop, shutdown = _span(events, 'loop'), _span(events, 'shutdown')
    # Edge case: shutdown starting exactly where the loop ends is fine.
    shutdown['t0'] = loop['t0'] + loop['dur']
    assert check_events(events) == []
    shutdown['t0'] -= 10.0  # now starts inside the loop
    assert any('overlap' in p for p in check_events(events))
    events = copy.deepcopy(good_events)
    _span(events, 'shutdown')['name'] = 'teardown'
    assert any('root spans must be phases' in p for p in check_events(events))
    events = copy.deepcopy(good_events)
    _span(events, 'shutdown')['name'] = 'init'
    assert any('more than once' in p for p in check_events(events))


def test_read_events_drops_a_torn_final_line_only(tmp_path, good_events):
    """A crash can tear the last line; a torn line elsewhere is corruption."""
    lines = [json.dumps(e) for e in good_events]
    tail = tmp_path / 'tail.jsonl'
    tail.write_text('\n'.join(lines) + '\n{"v": 1, "ev": "sp')
    assert read_events(tail) == good_events
    middle = tmp_path / 'middle.jsonl'
    middle.write_text('\n'.join(lines[:3] + ['{"v": 1,'] + lines[3:]) + '\n')
    with pytest.raises(ValueError, match=r'middle\.jsonl:4'):
        read_events(middle)


def test_backend_choice_follows_the_scenario(run_fake):
    """The recorded backends change when the run takes a different solver path."""
    _, _, events = run_fake(
        {'aragog_solver': 'radau', 'zalmoxis_backend_loop': 'numpy'}, iters=3
    )
    backend = next(e for e in events if e['ev'] == 'backend')
    assert (backend['submodule'], backend['value']) == ('aragog', 'radau')
    loop = attributed_totals(events)['loop']['attributed']
    assert ('structure', 'zalmoxis', 'numpy') in loop
    assert ('structure', 'zalmoxis', 'jax') not in loop
    assert FAKE_DEFAULTS['aragog_solver'] == 'cvode'  # the default really differs
