from __future__ import annotations

from unittest.mock import patch

from unreal_mcp.tools import replication


def _found(**fields):
    return {"found": True, "error": None, **fields}


def test_dormancy_names_match_the_live_enum():
    # Measured members are DORM_AWAKE, DORM_DORMANT_ALL, DORM_DORMANT_PARTIAL,
    # DORM_INITIAL, DORM_NEVER. All-caps with underscores; DORM_Awake is a guess
    # from the C++ prose and raises AttributeError.
    assert replication.DORMANCY["dormant"] == "DORM_DORMANT_ALL"
    assert replication.DORMANCY["awake"] == "DORM_AWAKE"
    assert replication.DORMANCY["initial"] == "DORM_INITIAL"
    assert replication.DORMANCY["dormant_partial"] == "DORM_DORMANT_PARTIAL"
    assert replication.DORMANCY["never"] == "DORM_NEVER"


def test_bad_dormancy_is_refused_before_dispatch():
    with patch.object(replication, "get_bridge") as bridge:
        result = replication.set_replication_flags("A", net_dormancy="DORM_Awake")
        bridge.assert_not_called()
    assert result["success"] is False
    assert "dormant_partial" in result["error"]


def test_no_op_is_refused_before_dispatch():
    with patch.object(replication, "get_bridge") as bridge:
        result = replication.set_replication_flags("A")
        bridge.assert_not_called()
    assert result["success"] is False
    assert "Nothing to do" in result["error"]


def test_actor_flags_use_the_dedicated_setters():
    # set_editor_property raises "cannot be edited on instances" for bReplicates
    # and bReplicateMovement, so the generated snippet must not use it for those.
    with patch.object(replication, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            actor_name="A",
            before={"replicates": False}, after={"replicates": True})
        replication.set_replication_flags("A", replicates=True,
                                          replicate_movement=True)
    body = bridge.return_value.run_python.call_args[0][0]
    assert "a.set_replicates(True)" in body
    assert "a.set_replicate_movement(True)" in body
    assert "set_editor_property('replicates'" not in body
    assert "set_editor_property('replicate_movement'" not in body


def test_dormancy_uses_set_net_dormancy():
    # set_editor_property('net_dormancy') does not persist; the setter does.
    with patch.object(replication, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            actor_name="A", before={}, after={})
        replication.set_replication_flags("A", net_dormancy="dormant")
    body = bridge.return_value.run_python.call_args[0][0]
    assert "a.set_net_dormancy(" in body
    assert "DORM_DORMANT_ALL" in body
    assert "set_editor_property('net_dormancy'" not in body


def test_component_replication_uses_set_is_replicated():
    # Reading a component is `replicates`; writing it is the set_is_replicated
    # method. There is no is_replicated property.
    with patch.object(replication, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            actor_name="A", before={}, after={})
        replication.set_replication_flags("A", replicate_components=True)
        body = bridge.return_value.run_python.call_args[0][0]
    assert "c.set_is_replicated(True)" in body


def test_component_rows_are_read_via_the_replicates_property():
    with patch.object(replication, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            actor_name="A", actor_class="StaticMeshActor", replicates=True,
            replicate_movement=False, net_dormancy="<NetDormancy.DORM_AWAKE: 1>",
            component_count=1, replicated_component_count=1,
            components=[{"name": "StaticMeshComponent0",
                         "class": "StaticMeshComponent", "replicates": True}])
        result = replication.get_replication_state("A")
    body = bridge.return_value.run_python.call_args[0][0]
    # guarded() embeds the body as a repr'd string literal, so the quotes inside
    # are backslash-escaped. Match the escaped form rather than reading the
    # snippet as if it were plain source.
    plain = body.replace("\\", "")
    assert "get_editor_property('replicates')" in plain
    # The read path must never ask for the non-existent is_replicated property.
    assert "get_editor_property('is_replicated')" not in plain
    assert "row[\'is_replicated\']" not in body
    assert result["replicated_component_count"] == 1


def test_missing_actor_is_reported_not_raised():
    with patch.object(replication, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = {
            "found": False, "error": "No actor named 'Nope' in the current level"}
        assert replication.get_replication_state("Nope")["success"] is False
        assert replication.set_replication_flags(
            "Nope", replicates=True)["success"] is False


def test_return_reports_before_and_after_from_the_editor():
    # The result must be evidence read back, not the arguments restated.
    with patch.object(replication, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            actor_name="A",
            before={"replicates": False, "replicated_components": 0},
            after={"replicates": True, "replicated_components": 2},
            component_replication_requested=True)
        result = replication.set_replication_flags("A", replicates=True)
    assert result["before"]["replicates"] is False
    assert result["after"]["replicates"] is True
    assert result["after"]["replicated_components"] == 2
    assert result["component_replication_requested"] is True