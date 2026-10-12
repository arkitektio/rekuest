"""kulisse: a URDF becomes a model, deterministically, and a model is held to the rules."""

import json
import struct
from pathlib import Path

import pytest

pytest.importorskip("trimesh")

from rekuest import kulisse
from rekuest.kulisse.cli import main
from rekuest.kulisse.urdf import UrdfImportError
from rekuest.kulisse.validate import ModelPolicy, ModelRefused, model_ref, validate_model

ARM = """<?xml version="1.0"?>
<robot name="arm">
  <material name="steel"><color rgba="0.6 0.6 0.65 1"/></material>
  <link name="base"><visual><geometry><box size="0.2 0.2 0.05"/></geometry><material name="steel"/></visual></link>
  <link name="upper"><visual><origin xyz="0 0 0.15"/><geometry><cylinder radius="0.02" length="0.3"/></geometry><material name="steel"/></visual></link>
  <link name="hand"><visual><geometry><sphere radius="0.03"/></geometry><material name="red"><color rgba="1 0 0 1"/></material></visual></link>
  <link name="finger_l"/>
  <link name="finger_r"/>
  <link name="tool0"/>
  <joint name="shoulder" type="revolute"><parent link="base"/><child link="upper"/><origin xyz="0 0 0.05" rpy="0 0 1.5708"/><axis xyz="0 0 2"/><limit lower="-2" upper="2"/></joint>
  <joint name="wrist" type="continuous"><parent link="upper"/><child link="hand"/><origin xyz="0 0 0.3"/><axis xyz="0 1 0"/></joint>
  <joint name="grip_l" type="prismatic"><parent link="hand"/><child link="finger_l"/><axis xyz="1 0 0"/><limit lower="0" upper="0.04"/></joint>
  <joint name="grip_r" type="prismatic"><parent link="hand"/><child link="finger_r"/><axis xyz="1 0 0"/><limit lower="-0.04" upper="0"/><mimic joint="grip_l" multiplier="-1"/></joint>
  <joint name="flange" type="fixed"><parent link="hand"/><child link="tool0"/><origin xyz="0 0 0.05"/></joint>
</robot>
"""


def document(data: bytes) -> dict:
    (length,) = struct.unpack_from("<I", data, 12)
    return json.loads(data[20 : 20 + length])


@pytest.fixture
def arm(tmp_path: Path) -> Path:
    path = tmp_path / "arm.urdf"
    path.write_text(ARM)
    return path


def test_identical_input_gives_identical_bytes(arm: Path) -> None:
    assert kulisse.import_urdf(arm) == kulisse.import_urdf(arm)


def test_an_imported_robot_is_a_valid_model_with_its_joint_table(arm: Path) -> None:
    data = kulisse.import_urdf(arm)
    report = validate_model(data, model_ref(data))

    assert report.warnings == []
    assert {"base", "upper", "hand", "tool0", "shoulder", "wrist", "grip_l", "grip_r"} <= set(report.nodes)
    assert [joint["name"] for joint in report.joints] == ["shoulder", "wrist", "grip_l", "grip_r"]
    shoulder, wrist, _, grip_r = report.joints
    assert shoulder == {"name": "shoulder", "node": "shoulder", "type": "revolute", "axis": [0.0, 0.0, 1.0], "limit": {"lower": -2.0, "upper": 2.0}}
    assert "limit" not in wrist
    assert grip_r["mimic"] == {"joint": "grip_l", "multiplier": -1.0, "offset": 0.0}
    assert report.stats.triangles > 100


def test_links_and_joints_become_the_node_tree(arm: Path) -> None:
    nodes = document(kulisse.import_urdf(arm))["nodes"]
    by_name = {node["name"]: node for node in nodes}
    child_names = lambda name: [nodes[index]["name"] for index in by_name[name].get("children", [])]  # noqa: E731

    # A moving joint is a pivot at the joint origin with the child link beneath it...
    assert "shoulder" in child_names("base") and child_names("shoulder") == ["upper"]
    assert by_name["shoulder"]["translation"] == [0.0, 0.0, 0.05]
    assert "translation" not in by_name["upper"]
    # ...and a fixed joint is plain parent and child, carrying the joint's origin.
    assert "tool0" in child_names("hand") and by_name["tool0"]["translation"] == [0.0, 0.0, 0.05]
    assert "flange" not in by_name
    # Z-up is turned into Y-up once, at the root.
    assert by_name["base"]["rotation"] == pytest.approx([-0.707106781, 0.0, 0.0, 0.707106781])


def test_the_source_urdf_is_named_by_its_hash_and_shared_materials_are_written_once(arm: Path) -> None:
    exported = document(kulisse.import_urdf(arm))
    assert exported["extras"]["arkitekt"]["source"].startswith("sha256:")
    assert len(exported["materials"]) == 2
    assert exported["asset"]["generator"] == "kulisse 1"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('type="continuous"', 'type="floating"'), "is floating"),
        (('<link name="tool0"/>', '<link name="tool0"/><link name="wrist"/>'), "share a name"),
        (('<parent link="hand"/><child link="tool0"/>', '<parent link="hand"/><child link="nowhere"/>'), "one root link"),
    ],
)
def test_what_cannot_be_a_model_fails_the_import(tmp_path: Path, change: tuple[str, str], message: str) -> None:
    path = tmp_path / "bad.urdf"
    path.write_text(ARM.replace(*change))
    with pytest.raises(UrdfImportError, match=message):
        kulisse.import_urdf(path)


def test_mesh_files_are_resolved_through_package_roots(tmp_path: Path) -> None:
    import trimesh

    package = tmp_path / "my_arm"
    (package / "meshes").mkdir(parents=True)
    trimesh.creation.box(extents=(1, 1, 1)).export(package / "meshes" / "cube.stl")
    urdf = tmp_path / "cube.urdf"
    urdf.write_text('<robot name="c"><link name="cube"><visual><geometry><mesh filename="package://my_arm/meshes/cube.stl" scale="0.1 0.1 0.1"/></geometry></visual></link></robot>')

    with pytest.raises(UrdfImportError, match="--package my_arm="):
        kulisse.import_urdf(urdf)
    data = kulisse.import_urdf(urdf, packages={"my_arm": package})
    assert validate_model(data, model_ref(data)).stats.triangles == 12
    assert document(data)["accessors"][0]["max"] == pytest.approx([0.05, 0.05, 0.05])


def test_a_joint_table_can_be_written_into_any_glb(arm: Path) -> None:
    plain = kulisse.with_joints(kulisse.import_urdf(arm), [])
    assert validate_model(plain, model_ref(plain)).joints == []

    table = [{"name": "lid", "node": "upper", "type": "revolute", "axis": [1, 0, 0], "limit": {"lower": 0, "upper": 1.5}}]
    jointed = kulisse.with_joints(plain, table)
    assert [joint["name"] for joint in validate_model(jointed, model_ref(jointed)).joints] == ["lid"]
    with pytest.raises(ModelRefused, match="which the model does not have"):
        bad = kulisse.with_joints(plain, [{**table[0], "node": "nowhere"}])
        validate_model(bad, model_ref(bad))


def test_the_command_imports_checks_and_writes_the_snippet(arm: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "arm.glb"
    assert main(["import", str(arm), "--out", str(out)]) == 0
    ref = kulisse.reference(out)
    assert f"wrote {out}  {ref}" in capsys.readouterr().out

    assert main(["check", str(out)]) == 0
    assert "4 joints" in capsys.readouterr().out

    assert main(["snippet", str(out)]) == 0
    assert capsys.readouterr().out.strip() == '<Robot src="@self.assets.arm" joints="@self.State.joints" />'
    assert main(["snippet", str(out), "--name", "robot"]) == 0
    assert capsys.readouterr().out.strip() == '<Robot src="@self.assets.robot" joints="@self.State.joints" />'

    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"maxTriangles": 10}))
    assert main(["check", str(out), "--policy", str(policy)]) == 1
    assert "refused (over_budget)" in capsys.readouterr().err


def test_textures_are_budgeted_from_their_headers() -> None:
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 8192, 64)
    payload = json.dumps({"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(png)}], "bufferViews": [{"buffer": 0, "byteLength": len(png)}], "images": [{"bufferView": 0}]}).encode()
    payload += b" " * (-len(payload) % 4)
    chunks = struct.pack("<II", len(payload), 0x4E4F534A) + payload + struct.pack("<II", len(png), 0x004E4942) + png
    data = struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks

    with pytest.raises(ModelRefused, match="8192 x 64"):
        validate_model(data, model_ref(data))
    assert validate_model(data, model_ref(data), ModelPolicy(max_texture_size=8192)).stats.textures == 1
