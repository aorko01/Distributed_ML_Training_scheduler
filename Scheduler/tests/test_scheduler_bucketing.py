"""Unit tests for Scheduler/app/services/scheduler_service.py bucketing functions."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timezone, timedelta

from app.services.scheduler_service import (
    _completion_time,
    _bucket_daily,
    _bucket_weekly,
    _bucket_monthly,
    _bucket_yearly,
)


class TestCompletionTime:
    def test_returns_updated_at(self, sample_job):
        sample_job.updated_at = datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
        result = _completion_time(sample_job)
        assert result == datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc)

    def test_fallback_to_created_at(self, sample_job):
        sample_job.updated_at = None
        sample_job.created_at = datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        result = _completion_time(sample_job)
        assert result == datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc)

    def test_none_when_both_none(self, sample_job):
        sample_job.updated_at = None
        sample_job.created_at = None
        result = _completion_time(sample_job)
        assert result is None

    def test_naive_datetime_gets_utc(self, sample_job):
        sample_job.updated_at = datetime(2024, 6, 15, 14, 30, 0)
        result = _completion_time(sample_job)
        assert result.tzinfo == timezone.utc


class TestBucketDaily:
    def test_empty_list(self):
        result = _bucket_daily([])
        assert len(result) == 6
        assert all(item["jobs"] == 0 for item in result)

    def test_jobs_in_buckets(self):
        now = datetime.now(timezone.utc)
        completed = [
            (now.replace(hour=0, minute=30), "job1"),
            (now.replace(hour=1, minute=15), "job2"),
            (now.replace(hour=5, minute=0), "job3"),
            (now.replace(hour=8, minute=45), "job4"),
        ]
        result = _bucket_daily(completed)
        assert result[0]["label"] == "00:00"
        assert result[0]["jobs"] == 2
        assert result[1]["label"] == "04:00"
        assert result[1]["jobs"] == 1

    def test_labels_are_4_hour_intervals(self):
        result = _bucket_daily([])
        labels = [item["label"] for item in result]
        assert labels == ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"]


class TestBucketWeekly:
    def test_empty_list(self):
        result = _bucket_weekly([])
        assert len(result) == 7
        assert all(item["jobs"] == 0 for item in result)

    def test_jobs_in_buckets(self):
        now = datetime.now(timezone.utc)
        today = now.date()
        completed = [
            (datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc), "job1"),
            (datetime.combine(today - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc), "job2"),
            (datetime.combine(today - timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc), "job3"),
        ]
        result = _bucket_weekly(completed)
        today_bucket = [item for item in result if item["jobs"] > 0]
        assert len(today_bucket) == 3

    def test_labels_are_day_names(self):
        result = _bucket_weekly([])
        labels = [item["label"] for item in result]
        for label in labels:
            assert len(label) == 3


class TestBucketMonthly:
    def test_empty_list(self):
        result = _bucket_monthly([])
        assert len(result) == 4
        assert all(item["label"].startswith("Week") for item in result)

    def test_jobs_in_buckets(self):
        now = datetime.now(timezone.utc)
        # Get the start of the current week (Monday)
        this_week_start = now - timedelta(days=now.weekday())
        this_week_start = this_week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        completed = [
            (this_week_start, "job1"),
        ]
        result = _bucket_monthly(completed)
        # At least one bucket should have jobs
        assert any(item["jobs"] > 0 for item in result)


class TestBucketYearly:
    def test_empty_list(self):
        result = _bucket_yearly([])
        assert len(result) == 12
        assert all(item["jobs"] == 0 for item in result)

    def test_labels_are_month_abbreviations(self):
        result = _bucket_yearly([])
        labels = [item["label"] for item in result]
        expected = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        assert labels == expected

    def test_jobs_in_correct_month(self):
        now = datetime.now(timezone.utc)
        # Use the current year to ensure the buckets match
        completed = [
            (datetime(now.year, 3, 15, tzinfo=timezone.utc), "job1"),
            (datetime(now.year, 3, 20, tzinfo=timezone.utc), "job2"),
            (datetime(now.year, 7, 10, tzinfo=timezone.utc), "job3"),
        ]
        result = _bucket_yearly(completed)
        assert result[2]["label"] == "Mar"
        assert result[2]["jobs"] == 2
        assert result[6]["label"] == "Jul"
        assert result[6]["jobs"] == 1
