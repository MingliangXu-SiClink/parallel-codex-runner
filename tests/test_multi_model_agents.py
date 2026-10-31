"""Tests for multi-model candidate agent specs."""

from __future__ import annotations

import pytest
from parallel_codex_runner_core.models import (
    AgentSpec,
    normalize_agent_specs,
    expand_agent_specs,
    agent_spec_by_index,
)
from parallel_codex_runner_core.app import agent_spec_execution_args
import argparse


class TestAgentSpec:
    def test_default_values(self):
        spec = AgentSpec()
        assert spec.model is None
        assert spec.effort is None
        assert spec.fast is None
        assert spec.count == 1

    def test_with_defaults_fills_none_fields(self):
        spec = AgentSpec(model="codex-6.1-sol")
        filled = spec.with_defaults("default-model", "high", True)
        assert filled.model == "codex-6.1-sol"
        assert filled.effort == "high"
        assert filled.fast is True
        assert filled.count == 1

    def test_with_defaults_preserves_explicit_fields(self):
        spec = AgentSpec(model="o3", effort="medium", fast=False, count=2)
        filled = spec.with_defaults("default-model", "high", True)
        assert filled.model == "o3"
        assert filled.effort == "medium"
        assert filled.fast is False
        assert filled.count == 2

    def test_to_dict_omits_none_fields(self):
        spec = AgentSpec(model="o3", count=3)
        d = spec.to_dict()
        assert d == {"count": 3, "model": "o3"}
        assert "effort" not in d
        assert "fast" not in d

    def test_to_dict_includes_all_set_fields(self):
        spec = AgentSpec(model="o3", effort="high", fast=True, count=2)
        d = spec.to_dict()
        assert d == {"count": 2, "model": "o3", "effort": "high", "fast": True}

    def test_from_mapping_parses_valid_input(self):
        spec = AgentSpec.from_mapping({"model": "codex-6.1-sol", "count": 3, "effort": "high"})
        assert spec is not None
        assert spec.model == "codex-6.1-sol"
        assert spec.count == 3
        assert spec.effort == "high"
        assert spec.fast is None

    def test_from_mapping_returns_none_for_non_dict(self):
        assert AgentSpec.from_mapping("not a dict") is None
        assert AgentSpec.from_mapping(123) is None
        assert AgentSpec.from_mapping(None) is None

    def test_from_mapping_defaults_count_to_1(self):
        spec = AgentSpec.from_mapping({"model": "o3"})
        assert spec is not None
        assert spec.count == 1

    def test_from_mapping_normalizes_effort(self):
        spec = AgentSpec.from_mapping({"model": "o3", "effort": "HIGH"})
        assert spec is not None
        assert spec.effort == "high"

    def test_from_mapping_treats_empty_model_as_none(self):
        spec = AgentSpec.from_mapping({"model": "", "count": 2})
        assert spec is not None
        assert spec.model is None


class TestNormalizeAgentSpecs:
    def test_empty_list_returns_empty(self):
        assert normalize_agent_specs([]) == []

    def test_none_returns_empty(self):
        assert normalize_agent_specs(None) == []

    def test_non_list_returns_empty(self):
        assert normalize_agent_specs("not a list") == []
        assert normalize_agent_specs(123) == []

    def test_single_spec(self):
        specs = normalize_agent_specs([{"model": "o3", "count": 5}])
        assert len(specs) == 1
        assert specs[0].model == "o3"
        assert specs[0].count == 5

    def test_multiple_specs(self):
        specs = normalize_agent_specs([
            {"model": "codex-6.1-sol", "count": 2},
            {"model": "codex-6-astra", "count": 3},
        ])
        assert len(specs) == 2
        assert specs[0].count == 2
        assert specs[1].count == 3

    def test_invalid_item_invalidates_whole_list(self):
        specs = normalize_agent_specs([
            {"model": "o3", "count": 2},
            "not a dict",
        ])
        assert specs == []


class TestExpandAgentSpecs:
    def test_expand_count_into_per_agent(self):
        specs = [AgentSpec(model="o3", count=3), AgentSpec(model="o4-mini", count=2)]
        expanded = expand_agent_specs(specs)
        assert len(expanded) == 5
        assert all(s.count == 1 for s in expanded)
        assert expanded[0].model == "o3"
        assert expanded[1].model == "o3"
        assert expanded[2].model == "o3"
        assert expanded[3].model == "o4-mini"
        assert expanded[4].model == "o4-mini"

    def test_expand_preserves_effort_and_fast(self):
        specs = [AgentSpec(model="o3", effort="high", fast=True, count=2)]
        expanded = expand_agent_specs(specs)
        assert all(s.effort == "high" and s.fast is True for s in expanded)

    def test_expand_empty_list(self):
        assert expand_agent_specs([]) == []


class TestAgentSpecByIndex:
    def test_maps_1_based_index(self):
        specs = [AgentSpec(model="o3", count=2), AgentSpec(model="o4-mini", count=3)]
        by_idx = agent_spec_by_index(specs)
        assert by_idx[1].model == "o3"
        assert by_idx[2].model == "o3"
        assert by_idx[3].model == "o4-mini"
        assert by_idx[4].model == "o4-mini"
        assert by_idx[5].model == "o4-mini"

    def test_empty_specs(self):
        assert agent_spec_by_index([]) == {}


class TestAgentSpecExecutionArgs:
    def _make_args(self, model=None, effort=None, fast=None):
        return argparse.Namespace(
            model=model, effort=effort, fast=fast,
            num_agents=5, prompt="test", workspace="/tmp",
        )

    def test_no_specs_returns_original_args(self):
        args = self._make_args(model="default-model")
        result = agent_spec_execution_args(args, None, 1)
        assert result is args

    def test_no_spec_for_index_returns_original_args(self):
        args = self._make_args(model="default-model")
        specs = {1: {"model": "o3"}}
        result = agent_spec_execution_args(args, specs, 2)
        assert result is args

    def test_spec_overrides_model(self):
        args = self._make_args(model="default-model")
        specs = {1: {"model": "o3"}}
        result = agent_spec_execution_args(args, specs, 1)
        assert result.model == "o3"

    def test_spec_overrides_effort(self):
        args = self._make_args(effort="low")
        specs = {2: {"effort": "high"}}
        result = agent_spec_execution_args(args, specs, 2)
        assert result.effort == "high"

    def test_spec_overrides_fast(self):
        args = self._make_args(fast=False)
        specs = {3: {"fast": True}}
        result = agent_spec_execution_args(args, specs, 3)
        assert result.fast is True

    def test_spec_preserves_unspecified_fields(self):
        args = self._make_args(model="default-model", effort="low", fast=False)
        specs = {1: {"model": "o3"}}
        result = agent_spec_execution_args(args, specs, 1)
        assert result.model == "o3"
        assert result.effort == "low"
        assert result.fast is False

    def test_spec_none_model_does_not_override(self):
        args = self._make_args(model="default-model")
        specs = {1: {"effort": "high"}}
        result = agent_spec_execution_args(args, specs, 1)
        assert result.model == "default-model"
        assert result.effort == "high"

    def test_spec_does_not_mutate_original_args(self):
        args = self._make_args(model="default-model")
        specs = {1: {"model": "o3"}}
        agent_spec_execution_args(args, specs, 1)
        assert args.model == "default-model"
