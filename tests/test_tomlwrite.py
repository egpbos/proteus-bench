"""Tests for proteus_bench.tomlwrite.

Contract clauses: ``tomllib.loads(dumps(x)) == x`` for every type tomllib
returns (nested and empty tables, arrays of tables, escaped strings and keys,
ints, floats with exponents, inf and nan, bools, dates and times), including
every value form PROTEUS's all_options.toml uses; values tomllib cannot
produce raise TypeError.
"""

from __future__ import annotations

import datetime as dt
import math
import tomllib

import pytest

from proteus_bench.tomlwrite import dumps

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def roundtrip(value: dict) -> dict:
    return tomllib.loads(dumps(value))


# The value forms of PROTEUS's input/all_options.toml: a top-level key, tables
# three deep, exponent floats with and without sign or point, literal strings
PROTEUS_LIKE = """
config_version = "3.0"

[params]
resume = false

[params.stop.iters]
enabled = true
minimum = 5
maximum = 9000

[params.stop.time]
minimum = 1.0e3
maximum = 6.0e+9

[params.dt]
minimum_rel = 1e-5
initial = 3e1
evection_maximum = 'none'

[planet]
ini_dsdr = -4.698e-6
flux_guess = -1
mass_tot = 1.0

[orbit.obliqua]
n = [2]
m = [0, 2]

[accretion.morrigan]
masses = []
"""


def test_proteus_config_forms_round_trip():
    """Every value form PROTEUS's all_options.toml uses survives a round trip with its type."""
    config = tomllib.loads(PROTEUS_LIKE)
    back = roundtrip(config)
    assert back == config
    assert type(back['planet']['flux_guess']) is int
    assert type(back['planet']['mass_tot']) is float
    assert type(back['params']['dt']['initial']) is float  # 3e1, not the int 30
    assert back['params']['stop']['time']['maximum'] == pytest.approx(6e9, rel=1e-15)
    assert back['params']['dt']['evection_maximum'] == 'none'


def test_strings_and_keys_that_need_escaping():
    """Quotes, backslashes, control characters, DEL, unicode and odd keys come back intact."""
    tricky = 'a "quote" \\ back\ttab\nnewline \x00 \x1f \x7f é ☃ \U0001f600'
    value = {
        's': tricky,
        '': 'empty key',
        'dotted.key': 1,
        'spaced key': {'inner "q"': 'x'},
        'bare-key_1': '',
    }
    assert roundtrip(value) == value
    text = dumps(value)
    assert '\x7f' not in text  # TOML forbids a raw DEL
    assert '"dotted.key" = 1' in text  # quoted, so not read as a nested table


def test_numbers_keep_their_type_and_value():
    """Ints stay ints, integral floats stay floats, exponents and specials survive."""
    value = {
        'i': -42,
        'big': 2**62,
        'f': 100.0,
        'tiny': 1e-7,
        'huge': 1.5e16,
        'neg_zero': -0.0,
        'inf': math.inf,
        'ninf': -math.inf,
        't': True,
        'fa': False,
    }
    back = roundtrip(value)
    assert back == value
    assert type(back['f']) is float
    assert type(back['i']) is int
    assert type(back['t']) is bool
    assert math.copysign(1.0, back['neg_zero']) == pytest.approx(-1.0, abs=0)
    nan = roundtrip({'n': math.nan})['n']
    assert math.isnan(nan)


def test_tables_arrays_and_dates():
    """Empty tables, arrays of tables, nested arrays and all date-time kinds round-trip."""
    value = {
        'empty': {},
        'a': {'b': {'c': {'d': 1}}},
        'only_sub': {'x': {'y': []}},
        'rows': [{'name': 'one', 'n': 1}, {'name': 'two', 'sub': {'k': [1, 2]}}],
        'mixed': [[1, 2], ['a'], []],
        'when': dt.datetime(2026, 9, 25, 3, 10, 0, 123456, tzinfo=dt.UTC),
        'local': dt.datetime(2026, 9, 25, 3, 10),
        'day': dt.date(2026, 9, 25),
        'clock': dt.time(3, 10, 5),
    }
    assert roundtrip(value) == value
    assert roundtrip({}) == {}
    assert dumps({}) == ''


def test_unsupported_values_raise():
    """A value tomllib never produces is refused loudly, with its type in the message."""
    with pytest.raises(TypeError, match='NoneType'):
        dumps({'k': None})
    with pytest.raises(TypeError, match='tuple'):
        dumps({'t': {'deep': (1, 2)}})
