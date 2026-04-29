"""Tests for A/B testing service (NIF-238, NIF-262)."""

import uuid
import math

import pytest

from src.services.ab_testing import ABTestService


class TestDeterministicVariantAssignment:
    """Test the static hash-based variant assignment."""

    def test_same_input_same_output(self):
        """Same (experiment, lead) always returns the same variant."""
        exp_id = uuid.uuid4()
        lead_id = uuid.uuid4()
        variants = ["A", "B"]

        result1 = ABTestService._deterministic_variant(exp_id, lead_id, variants)
        result2 = ABTestService._deterministic_variant(exp_id, lead_id, variants)
        assert result1 == result2

    def test_different_leads_may_differ(self):
        """Different leads can get different variants (statistical — with enough leads, both appear)."""
        exp_id = uuid.uuid4()
        variants = ["A", "B"]
        assigned = set()
        for _ in range(100):
            lead_id = uuid.uuid4()
            assigned.add(ABTestService._deterministic_variant(exp_id, lead_id, variants))
        # With 100 leads and 2 variants, both should appear
        assert len(assigned) == 2

    def test_different_experiment_different_assignment(self):
        """Same lead in different experiments can get different variants."""
        lead_id = uuid.uuid4()
        variants = ["A", "B"]
        assignments = set()
        for _ in range(50):
            exp_id = uuid.uuid4()
            assignments.add(ABTestService._deterministic_variant(exp_id, lead_id, variants))
        # Should see both variants across different experiments
        assert len(assignments) == 2

    def test_three_variants(self):
        """Works with three variants."""
        exp_id = uuid.uuid4()
        variants = ["A", "B", "C"]
        assigned = set()
        for _ in range(200):
            lead_id = uuid.uuid4()
            assigned.add(ABTestService._deterministic_variant(exp_id, lead_id, variants))
        assert len(assigned) == 3

    def test_result_always_valid(self):
        """Returned variant is always from the input list."""
        exp_id = uuid.uuid4()
        variants = ["control", "treatment"]
        for _ in range(50):
            result = ABTestService._deterministic_variant(exp_id, uuid.uuid4(), variants)
            assert result in variants


class TestZTestProportions:
    """Test the statistical significance calculation."""

    def test_identical_proportions(self):
        """Same proportions should give p-value of 1.0."""
        p = ABTestService._z_test_proportions(0.5, 100, 0.5, 100)
        assert p >= 0.99

    def test_very_different_proportions(self):
        """Very different proportions with large samples should be significant."""
        p = ABTestService._z_test_proportions(0.8, 1000, 0.2, 1000)
        assert p < 0.001

    def test_zero_sample_size(self):
        """Zero sample size returns p=1.0 (not significant)."""
        p = ABTestService._z_test_proportions(0.5, 0, 0.5, 100)
        assert p == 1.0

    def test_zero_proportions(self):
        """All-zero proportions return p=1.0."""
        p = ABTestService._z_test_proportions(0.0, 100, 0.0, 100)
        assert p == 1.0

    def test_borderline_significance(self):
        """Test p-value is a reasonable float between 0 and 1."""
        p = ABTestService._z_test_proportions(0.55, 200, 0.45, 200)
        assert 0.0 <= p <= 1.0

    def test_small_difference_not_significant(self):
        """Small difference with small sample should not be significant."""
        p = ABTestService._z_test_proportions(0.52, 30, 0.48, 30)
        assert p > 0.05
