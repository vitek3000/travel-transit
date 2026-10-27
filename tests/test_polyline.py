import pytest

from travel_transit.polyline import decode, encode, thin


def test_the_example_of_the_google_documentation_decodes_at_precision_5():
    assert decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@", 5) == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_precision_7_round_trips():
    line = [(50.8456540, 4.3568040), (50.6245500, 5.5666980), (50.8496000, 5.7058000)]
    assert decode(encode(line, 7), 7) == pytest.approx(line, abs=1e-7)


def test_negative_and_zero_steps_round_trip():
    line = [(-33.8688, 151.2093), (-33.8688, 151.2093), (0.0, 0.0), (-0.00001, -0.00001)]
    assert decode(encode(line, 6), 6) == pytest.approx(line, abs=1e-6)


def test_an_empty_string_is_no_points():
    assert decode("", 7) == []


@pytest.mark.parametrize("broken", ["_p~iF~ps|U_", "_p~iF~ps|U_ulLnnqC_mqNvxq`", "ab\x01cd"])
def test_a_cut_or_garbled_string_is_refused(broken):
    with pytest.raises(ValueError):
        decode(broken, 5)


def test_thinning_drops_the_points_of_a_straight_line_and_keeps_the_ends():
    straight = [(50.0 + i * 0.001, 4.0 + i * 0.001) for i in range(101)]
    assert thin(straight) == [(50.0, 4.0), (50.1, 4.1)]


def test_thinning_keeps_a_corner():
    corner = [(50.0, 4.0), (50.05, 4.0), (50.1, 4.0), (50.1, 4.05), (50.1, 4.1)]
    assert thin(corner) == [(50.0, 4.0), (50.1, 4.0), (50.1, 4.1)]


def test_thinning_keeps_at_most_the_number_asked_with_both_ends():
    zigzag = [(50.0 + i * 0.01, 4.0 + (0.01 if i % 2 else 0.0)) for i in range(500)]
    out = thin(zigzag, max_points=50)
    assert len(out) == 50
    assert out[0] == (50.0, 4.0) and out[-1] == (round(50.0 + 499 * 0.01, 5), 4.01)


def test_thinning_rounds_to_five_decimals_and_a_line_stays_a_line():
    assert thin([(50.1234567, 4.1234567), (50.1234568, 4.1234568), (50.1234569, 4.1234561)]) == [
        (50.12346, 4.12346),
        (50.12346, 4.12346),
    ]
