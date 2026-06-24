from rack import ProgressReporter


def test_smoke() -> None:
    reporter = ProgressReporter(test_id="test_L0_001_smoke", total=1, dut_kind="Fixture")
    reporter.start()
    reporter.progress(done=1, dut_id="smoke", force=True)
    assert True
    reporter.finish(done=1, failures=0)
