"""Deployment-wide model settings.

Which model suits a stage is learned by running it — this project moved three
stages onto different models on evidence in one afternoon, each time by
editing config and restarting. A setting that needs a deploy to change is one
nobody changes.
"""

from __future__ import annotations

import mongomock
import pytest

from app import config, db, settings
from app.agents import model_for


@pytest.fixture(autouse=True)
def store(monkeypatch):
    fake = mongomock.MongoClient().db
    monkeypatch.setattr(db, "db", lambda: fake)
    return fake


class TestResolutionOrder:
    def test_nothing_stored_uses_the_environment(self):
        assert settings.model_for_stage("ui") == config.default_model_for("ui")

    def test_a_stored_setting_beats_the_environment(self):
        settings.set_models({"ui": "acme/coder"}, "u1")
        assert settings.model_for_stage("ui") == "acme/coder"

    def test_an_artifact_override_beats_the_setting(self):
        """One requirement doing something unusual must not need a global change."""
        settings.set_models({"ui": "acme/coder"}, "u1")
        assert settings.model_for_stage("ui", {"modelOverrides": {"ui": "other/model"}}) == "other/model"

    def test_clearing_returns_to_the_environment(self):
        settings.set_models({"ui": "acme/coder"}, "u1")
        settings.set_models({"ui": ""}, "u1")
        assert settings.model_for_stage("ui") == config.default_model_for("ui")

    def test_the_agents_see_stored_settings(self):
        settings.set_models({"decomposition": "acme/big"}, "u1")
        assert model_for({}, "decomposition") == "acme/big"


class TestValidation:
    def test_an_unknown_agent_is_refused(self):
        with pytest.raises(ValueError, match="Unknown agent"):
            settings.set_models({"nonsense": "x/y"}, "u1")

    def test_one_bad_agent_stores_nothing(self):
        """Partial application would leave the deployment half-configured."""
        settings.set_models({"ui": "acme/coder"}, "u1")
        with pytest.raises(ValueError):
            settings.set_models({"brd": "x/y", "nope": "z"}, "u1")
        assert settings.stored_models() == {"ui": "acme/coder"}


class TestRolesView:
    def test_it_reports_where_each_choice_came_from(self):
        settings.set_models({"ui": "acme/coder"}, "u1")
        view = {r["role"]: r for r in settings.roles_view({"modelOverrides": {"brd": "one/off"}})}
        assert view["ui"]["source"] == "setting"
        assert view["brd"]["source"] == "artifact"
        assert view["decomposition"]["source"] == "environment"

    def test_only_the_four_agents_are_offered(self):
        """Internal stages are not the unit anyone thinks in."""
        assert {r["role"] for r in settings.roles_view()} == {"intake", "brd", "ui", "decomposition"}

    def test_every_agent_appears(self):
        assert {r["role"] for r in settings.roles_view()} == set(config.AGENT_ROLES)


class TestAgentsOwnTheirStages:
    def test_one_choice_covers_every_stage_an_agent_runs(self):
        """The intake agent both converses and structures; a chooser should
        not have to know that, or set it twice."""
        settings.set_models({"intake": "acme/small"}, "u1")
        assert settings.model_for_stage("chat") == "acme/small"
        assert settings.model_for_stage("requirement") == "acme/small"

    def test_the_screen_plan_does_not_follow_the_ui_agent(self):
        """Planning the screen set is schema-following, not coding, and both
        coder models tried on it failed — one returning its own field names,
        the other dropping route, purpose and keyElements from every screen."""
        settings.set_models({"ui": "acme/coder"}, "u1")
        assert settings.model_for_stage("ui") == "acme/coder"
        assert settings.model_for_stage("uiPlan") == config.default_model_for("uiPlan")

    def test_an_unowned_stage_still_resolves(self):
        assert settings.model_for_stage("uiPlan") == config.default_model_for("uiPlan")
