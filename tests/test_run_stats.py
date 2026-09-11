from collector.run_stats import build_run_stats, log_run_summary


class _CaptureLog:
    def __init__(self):
        self.lines = []

    def info(self, message, *args):
        self.lines.append(message % args)


def test_run_summary_reports_population_deltas_and_coverage():
    log = _CaptureLog()
    baseline = {
        "jobs": 100,
        "seek_jobs": 90,
        "jd_markers": 40,
        "seek_with_jd": 40,
        "seek_without_jd": 50,
        "seek_unavailable": 1,
        "coverage": {},
    }
    current = {
        "jobs": 110,
        "seek_jobs": 100,
        "jd_markers": 65,
        "seek_with_jd": 65,
        "seek_without_jd": 35,
        "seek_unavailable": 3,
        "coverage": {"ACT": {"status": "COMPLETE", "covered": 500, "reported": 500}},
    }

    log_run_summary(
        log,
        run_id=15,
        status="STOPPED",
        duration_seconds=123.4,
        baseline=baseline,
        current=current,
        partitions_processed=7,
        jd_totals={"attempted": 27, "stored": 25, "failed": 0, "unavailable": 2},
    )

    assert "RUN SUMMARY run_id=15 status=STOPPED" in log.lines[0]
    assert "delta_jobs=+10" in log.lines[0]
    assert "delta_jds=+25" in log.lines[0]
    assert "jd_failed=0" in log.lines[0]
    assert "jd_unavailable=2" in log.lines[0]
    assert "COVERAGE SUMMARY run_id=15 geography=ACT status=COMPLETE" in log.lines[1]

    stats = build_run_stats(
        duration_seconds=123.4,
        baseline=baseline,
        current=current,
        partitions_processed=7,
        jd_totals={"attempted": 27, "stored": 25, "failed": 0, "unavailable": 2},
    )
    assert stats["duration_seconds"] == 123.4
    assert stats["jobs_total"] == 110
    assert stats["jobs_added"] == 10
    assert stats["jds_added"] == 25
    assert stats["jd_attempted"] == 27
    assert stats["partitions_processed"] == 7
