"""Per-requirement model selection.

The deployment defaults are a starting point, not a decision: the right model
is a property of the work. A small internal tool does not need the model a
regulated platform's BRD deserves, and this project moved three stages onto
different models on evidence within a single afternoon.
"""

from __future__ import annotations

import pytest

from app import config
from app.agents import model_for
from app.routes import artifacts as routes

MD = {"id": "u-md", "tierId": "md", "isClient": False}
VP = {"id": "u-vp", "tierId": "vp", "isClient": False}
TL = {"id": "u-tl", "tierId": "tl", "isClient": False}
CLIENT = {"id": "u-c", "tierId": None, "isClient": True}

ARTIFACT = {
    "originator": {"userId": "u-md", "tierId": "md"},
    "approvalChain": [{"approverTiers": ["md", "ceo"]}, {"approverTiers": ["vp"]}],
}


class TestResolution:
    def test_an_override_wins(self):
        assert model_for({"models": {"ui": "qwen/qwen3-coder"}}, "ui") == "qwen/qwen3-coder"

    def test_an_unset_agent_falls_back_to_the_default(self):
        assert model_for({"models": {"ui": "x/y"}}, "brd") == config.default_model_for("brd")

    def test_no_overrides_at_all_is_fine(self):
        assert model_for({}, "decomposition") == config.default_model_for("decomposition")

    def test_an_empty_override_does_not_shadow_the_default(self):
        """Clearing a choice must return the stage to the default, not to ''."""
        assert model_for({"models": {"ui": ""}}, "ui") == config.default_model_for("ui")


class TestRoles:
    def test_every_agent_stage_has_a_default(self):
        for meta in config.AGENT_ROLES.values():
            for stage in meta["stages"]:
                assert config.default_model_for(stage)

    def test_every_role_is_described(self):
        """The picker shows these; a role with no explanation is a guess."""
        for role, meta in config.AGENT_ROLES.items():
            assert meta["label"] and meta["detail"]

    def test_the_catalogue_is_extensible_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("MODEL_CHOICES", "acme/new-model")
        assert any(c["model"] == "acme/new-model" for c in config.model_choices())

    def test_extending_does_not_duplicate_a_listed_model(self, monkeypatch):
        monkeypatch.setenv("MODEL_CHOICES", "qwen/qwen3-coder")
        models = [c["model"] for c in config.model_choices()]
        assert models.count("qwen/qwen3-coder") == 1


class TestWhoChooses:
    def test_the_originator_may(self):
        assert routes._may_choose_models(ARTIFACT, MD)

    def test_a_gate_zero_approver_may(self):
        assert routes._may_choose_models(ARTIFACT, {"id": "x", "tierId": "ceo", "isClient": False})

    def test_the_vp_may(self):
        assert routes._may_choose_models(ARTIFACT, VP)

    def test_a_team_lead_may_not(self):
        assert not routes._may_choose_models(ARTIFACT, TL)

    def test_a_client_may_not(self):
        """It trades cost against quality on Stark Digital's side of the engagement."""
        assert not routes._may_choose_models(ARTIFACT, CLIENT)
