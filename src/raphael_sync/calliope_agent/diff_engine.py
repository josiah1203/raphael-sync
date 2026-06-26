"""Shadow diff engine for Fusion design snapshots."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from raphael_audit.core.event_builder import build_geometry_event


def _index_by_id(items: list[dict[str, Any]] | None, key: str = "id") -> dict[str, dict[str, Any]]:
    if not items:
        return {}
    return {str(item[key]): item for item in items if key in item}


def _feature_signature(feature: dict[str, Any]) -> str:
    # Feature-tree level diff via Native API (SolidWorks)
    if feature.get("tool_identifier") == "solidworks" or "native_tree_props" in feature:
        tree_props = feature.get("native_tree_props", {})
        return hashlib.sha256(json.dumps(tree_props, sort_keys=True).encode()).hexdigest()

    # Object model diff (Fusion 360) simulation
    props = feature.get("properties") or {}
    sig_parts = [
        str(feature.get("name", "")),
        str(feature.get("type", "")),
        str(sorted(props.items())),
    ]
    sig = "|".join(sig_parts)
    return hashlib.sha256(sig.encode()).hexdigest()


def diff_snapshots(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    session_id: str,
    user_id: str,
    tool_version: str = "unknown",
    emit_baseline: bool = True,
    application: str | None = "FormFlow",
) -> list[dict[str, Any]]:
    """Diff previous shadow snapshot against current and emit geometry events."""
    document_id = current["document_id"]
    document_name = current.get("document_name", "")
    project_id = current.get("project_id") or document_id
    timestamp_utc = current.get("captured_at_utc")
    causation_id = str(hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()[:12])

    base_payload = {
        "document_id": document_id,
        "document_name": document_name,
        "causation_id": causation_id,
    }

    if previous is None:
        if not emit_baseline:
            return []
        events: list[dict[str, Any]] = []
        for feature in current.get("features") or []:
            payload = {
                **base_payload,
                "feature_id": feature["id"],
                "feature_name": feature.get("name", ""),
                "feature_type": feature.get("type", ""),
                "properties": feature.get("properties") or {},
            }
            events.append(
                build_geometry_event(
                    event_type="geometry.feature_created",
                    payload=payload,
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tool_version=tool_version,
                    timestamp_utc=timestamp_utc, application=application,
                )
            )
        for material_id, material in (current.get("materials") or {}).items():
            events.append(
                build_geometry_event(
                    event_type="geometry.material_assigned",
                    payload={
                        **base_payload,
                        "material_id": material_id,
                        "material_name": material.get("name", ""),
                    },
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tool_version=tool_version,
                    timestamp_utc=timestamp_utc, application=application,
                )
            )
        for config in current.get("configurations") or []:
            events.append(
                build_geometry_event(
                    event_type="geometry.configuration_created",
                    payload={
                        **base_payload,
                        "configuration_id": config["id"],
                        "configuration_name": config.get("name", ""),
                    },
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tool_version=tool_version,
                    timestamp_utc=timestamp_utc, application=application,
                )
            )
        for joint in current.get("assembly_joints") or []:
            events.append(
                build_geometry_event(
                    event_type="geometry.assembly_mate_added",
                    payload={
                        **base_payload,
                        "joint_id": joint["id"],
                        "joint_name": joint.get("name", ""),
                        "joint_type": joint.get("type", ""),
                    },
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tool_version=tool_version,
                    timestamp_utc=timestamp_utc, application=application,
                )
            )
        return events

    events = []
    prev_features = _index_by_id(previous.get("features"))
    curr_features = _index_by_id(current.get("features"))

    for feature_id, feature in curr_features.items():
        if feature_id not in prev_features:
            events.append(
                build_geometry_event(
                    event_type="geometry.feature_created",
                    payload={
                        **base_payload,
                        "feature_id": feature_id,
                        "feature_name": feature.get("name", ""),
                        "feature_type": feature.get("type", ""),
                        "properties": feature.get("properties") or {},
                    },
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tool_version=tool_version,
                    timestamp_utc=timestamp_utc, application=application,
                )
            )
        elif _feature_signature(prev_features[feature_id]) != _feature_signature(feature):
            prev_feat = prev_features[feature_id]
            events.append(
                build_geometry_event(
                    event_type="geometry.feature_modified",
                    payload={
                        **base_payload,
                        "feature_id": feature_id,
                        "feature_name": feature.get("name", ""),
                        "feature_type": feature.get("type", ""),
                        "old_parameters": prev_feat.get("properties") or {},
                        "new_parameters": feature.get("properties") or {},
                        "properties": feature.get("properties") or {},
                    },
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tool_version=tool_version,
                    timestamp_utc=timestamp_utc, application=application,
                )
            )

    for feature_id, feature in prev_features.items():
        if feature_id not in curr_features:
            events.append(
                build_geometry_event(
                    event_type="geometry.feature_deleted",
                    payload={
                        **base_payload,
                        "feature_id": feature_id,
                        "feature_name": feature.get("name", ""),
                        "feature_type": feature.get("type", ""),
                    },
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tool_version=tool_version,
                    timestamp_utc=timestamp_utc, application=application,
                )
            )

    prev_materials = previous.get("materials") or {}
    curr_materials = current.get("materials") or {}
    for material_id, material in curr_materials.items():
        if material_id not in prev_materials or prev_materials[material_id] != material:
            # Body-level material diff payloads
            old_mat = prev_materials.get(material_id, {})
            events.append(
                build_geometry_event(
                    event_type="geometry.material_assigned",
                    payload={
                        **base_payload,
                        "material_id": material_id,
                        "material_name": material.get("name", ""),
                        "old_material_name": old_mat.get("name", ""),
                        "appearance": material.get("appearance", ""),
                        "old_appearance": old_mat.get("appearance", ""),
                    },
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tool_version=tool_version,
                    timestamp_utc=timestamp_utc, application=application,
                )
            )

    prev_configs = _index_by_id(previous.get("configurations"))
    curr_configs = _index_by_id(current.get("configurations"))
    for config_id, config in curr_configs.items():
        if config_id not in prev_configs:
            events.append(
                build_geometry_event(
                    event_type="geometry.configuration_created",
                    payload={
                        **base_payload,
                        "configuration_id": config_id,
                        "configuration_name": config.get("name", ""),
                    },
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tool_version=tool_version,
                    timestamp_utc=timestamp_utc, application=application,
                )
            )

    prev_joints = _index_by_id(previous.get("assembly_joints"))
    curr_joints = _index_by_id(current.get("assembly_joints"))
    for joint_id, joint in curr_joints.items():
        if joint_id not in prev_joints:
            events.append(
                build_geometry_event(
                    event_type="geometry.assembly_mate_added",
                    payload={
                        **base_payload,
                        "joint_id": joint_id,
                        "joint_name": joint.get("name", ""),
                        "joint_type": joint.get("type", ""),
                    },
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tool_version=tool_version,
                    timestamp_utc=timestamp_utc, application=application,
                )
            )

    return events
