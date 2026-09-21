package governed_factory.gate

import rego.v1

gates := {
	1: {"name": "Scope", "roles": {"product_owner"}, "outcomes": {"approve", "revise", "discard"}, "excludes_originator": true},
	2: {"name": "BRD", "roles": {"business_owner", "client_tech_lead"}, "outcomes": {"approve", "revise", "discard"}, "excludes_originator": true},
	3: {"name": "Design", "roles": {"architect", "ui_ux", "business_analyst"}, "outcomes": {"approve", "revise"}, "excludes_originator": true},
	4: {"name": "Plan", "roles": {"tech_lead", "stream_lead"}, "outcomes": {"approve", "revise"}, "excludes_originator": true},
	5: {"name": "Merge", "roles": {"senior_engineer"}, "outcomes": {"approve", "request_changes"}, "excludes_originator": true},
	6: {"name": "UAT", "roles": {"business_stakeholder"}, "outcomes": {"approve", "reject"}, "excludes_originator": false},
	7: {"name": "Release", "roles": {"release_manager"}, "outcomes": {"approve", "hold"}, "excludes_originator": true},
}

definition := object.get(gates, input.gate, null)
actor_roles := {trim_space(lower(role)) |
	some role in object.get(input.actor, "roles", [])
	trim_space(role) != ""
}
entitled := actor_roles & object.get(definition, "roles", set())
signatures := object.get(
	object.get(object.get(input.requirement, "gates", {}), sprintf("%v", [input.gate]), {}),
	"signatures",
	[],
)

denials contains sprintf("there is no gate %v", [input.gate]) if {
	definition == null
}

denials contains sprintf("outcome %q is not permitted at gate %v", [input.outcome, input.gate]) if {
	definition != null
	not input.outcome in definition.outcomes
}

denials contains sprintf("gate %v is not open; waiting at gate %v", [input.gate, input.expected_gate]) if {
	input.expected_gate != null
	input.gate != input.expected_gate
}

denials contains sprintf("actor holds no role entitled at gate %v", [input.gate]) if {
	definition != null
	count(entitled) == 0
}

denials contains sprintf("originator cannot approve gate %v", [input.gate]) if {
	definition != null
	definition.excludes_originator
	object.get(input.actor, "id", "") == object.get(object.get(input.requirement, "originator", {}), "id", "")
	object.get(input.actor, "id", "") != ""
}

denials contains sprintf("actor has already signed gate %v", [input.gate]) if {
	actor_id := object.get(input.actor, "id", "")
	some signature in signatures
	object.get(signature, "actorId", "") == actor_id
}

verdict := {
	"allowed": count(denials) == 0,
	"reason": concat("; ", sort([reason | some reason in denials])),
}
