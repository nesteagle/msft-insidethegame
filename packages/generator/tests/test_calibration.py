"""Season-level realism: averages must land inside the target ranges."""

import pytest

from footballfoundry.calibration import out_of_range, report, simulate_sample, summarize


@pytest.mark.slow
def test_season_averages_in_range():
    _, results = simulate_sample(380, seed=42, workers=0)
    summary = summarize(results)
    assert not out_of_range(summary), "\n" + report(summary)


@pytest.mark.slow
def test_calibration_holds_for_another_world():
    _, results = simulate_sample(380, seed=2027, workers=0)
    summary = summarize(results)
    assert not out_of_range(summary), "\n" + report(summary)
