"""URDF and Xacro to a model: a GLB whose nodes are the robot's links and joints.

One node per link; for each joint that moves, a pivot node at the joint's origin, named
after the joint, with the child link beneath it; a fixed joint is plain parent and child.
The joints are written into ``extras.arkitekt`` with URDF's own semantics, so nothing is
lost on the way. Collision, inertial and simulator tags are dropped.

Identical input must give identical bytes, or the same robot ends up as two models. The
GLB is therefore written here, in document order, with nothing in it that depends on the
time, the machine or a dictionary's mood.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

GENERATOR = "kulisse 1"
"""Written into ``asset.generator``. Changes only when the bytes this module writes change."""

MOVING = ("revolute", "continuous", "prismatic")


class UrdfImportError(ValueError):
    """A URDF this importer cannot turn into a model, with the reason."""


def _floats(text: str | None, count: int, default: tuple[float, ...]) -> tuple[float, ...]:
    if text is None:
        return default
    values = tuple(float(part) for part in text.split())
    if len(values) != count:
        raise UrdfImportError(f"expected {count} numbers, got {text!r}")
    return values


def _quaternion(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    """URDF's fixed-axis roll, pitch, yaw as a glTF quaternion (x, y, z, w)."""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def _multiply(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _clean(values: tuple[float, ...]) -> list[float]:
    """Numbers as they are written: rounded past float noise, and never a negative zero."""
    return [0.0 if abs(value) < 1e-12 else round(value, 9) for value in values]


@dataclass
class Origin:
    xyz: tuple[float, ...] = (0.0, 0.0, 0.0)
    rpy: tuple[float, ...] = (0.0, 0.0, 0.0)

    @classmethod
    def parse(cls, element: ET.Element | None) -> "Origin":
        if element is None:
            return cls()
        return cls(_floats(element.get("xyz"), 3, (0.0, 0.0, 0.0)), _floats(element.get("rpy"), 3, (0.0, 0.0, 0.0)))

    def node(self) -> dict[str, Any]:
        """The transform as glTF node properties, left out where it is the identity."""
        out: dict[str, Any] = {}
        if any(self.xyz):
            out["translation"] = _clean(self.xyz)
        if any(self.rpy):
            out["rotation"] = _clean(_quaternion(*self.rpy))
        return out


@dataclass
class Builder:
    """A glTF document and its binary chunk, grown in the order things are added."""

    nodes: list[dict[str, Any]] = field(default_factory=list)
    meshes: list[dict[str, Any]] = field(default_factory=list)
    materials: list[dict[str, Any]] = field(default_factory=list)
    accessors: list[dict[str, Any]] = field(default_factory=list)
    views: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    textures: list[dict[str, Any]] = field(default_factory=list)
    binary: bytearray = field(default_factory=bytearray)
    material_index: dict[str, int] = field(default_factory=dict)

    def view(self, data: bytes, target: int | None = None) -> int:
        self.binary.extend(b"\0" * (-len(self.binary) % 4))
        view: dict[str, Any] = {"buffer": 0, "byteOffset": len(self.binary), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        self.binary.extend(data)
        self.views.append(view)
        return len(self.views) - 1

    def accessor(self, array: np.ndarray, kind: str, component: int, target: int, bounds: bool = False) -> int:
        accessor: dict[str, Any] = {
            "bufferView": self.view(array.tobytes(), target),
            "componentType": component,
            "count": int(array.shape[0]),
            "type": kind,
        }
        if bounds:
            accessor["min"] = [float(value) for value in array.min(axis=0)]
            accessor["max"] = [float(value) for value in array.max(axis=0)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    def mesh(self, vertices: np.ndarray, normals: np.ndarray, faces: np.ndarray, material: int | None) -> int:
        primitive: dict[str, Any] = {
            "attributes": {
                "POSITION": self.accessor(vertices.astype("<f4"), "VEC3", 5126, 34962, bounds=True),
                "NORMAL": self.accessor(normals.astype("<f4"), "VEC3", 5126, 34962),
            },
            "indices": self.accessor(faces.astype("<u4").reshape(-1), "SCALAR", 5125, 34963),
        }
        if material is not None:
            primitive["material"] = material
        self.meshes.append({"primitives": [primitive]})
        return len(self.meshes) - 1

    def material(self, key: str, rgba: tuple[float, ...] | None, texture: Path | None) -> int:
        known = self.material_index.get(key)
        if known is not None:
            return known
        pbr: dict[str, Any] = {"metallicFactor": 0.1, "roughnessFactor": 0.7}
        if rgba is not None:
            pbr["baseColorFactor"] = _clean(rgba)
        if texture is not None:
            data = texture.read_bytes()
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(texture.suffix.lower())
            if mime is None:
                raise UrdfImportError(f"texture {texture.name} is not a PNG, JPEG or WebP")
            self.images.append({"bufferView": self.view(data), "mimeType": mime})
            self.textures.append({"source": len(self.images) - 1})
            pbr["baseColorTexture"] = {"index": len(self.textures) - 1}
        material: dict[str, Any] = {"pbrMetallicRoughness": pbr}
        if rgba is not None and rgba[3] < 1:
            material["alphaMode"] = "BLEND"
        self.materials.append(material)
        self.material_index[key] = len(self.materials) - 1
        return self.material_index[key]

    def node(self, properties: dict[str, Any]) -> int:
        self.nodes.append(properties)
        return len(self.nodes) - 1

    def glb(self, root: int, extras: dict[str, Any]) -> bytes:
        document: dict[str, Any] = {
            "asset": {"version": "2.0", "generator": GENERATOR},
            "scene": 0,
            "scenes": [{"nodes": [root]}],
            "nodes": self.nodes,
        }
        for key, value in (
            ("meshes", self.meshes),
            ("materials", self.materials),
            ("textures", self.textures),
            ("images", self.images),
            ("accessors", self.accessors),
            ("bufferViews", self.views),
        ):
            if value:
                document[key] = value
        binary = bytes(self.binary) + b"\0" * (-len(self.binary) % 4)
        if binary:
            document["buffers"] = [{"byteLength": len(binary)}]
        document["extras"] = extras
        payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        payload += b" " * (-len(payload) % 4)
        chunks = struct.pack("<II", len(payload), 0x4E4F534A) + payload
        if binary:
            chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
        return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


def expand(path: Path, arguments: dict[str, str] | None = None) -> str:
    """The URDF text of ``path``, with Xacro expanded when it is a Xacro file."""
    if path.suffix != ".xacro" and not arguments:
        return path.read_text(encoding="utf-8")
    try:
        import xacro
    except ImportError as error:  # pragma: no cover - the extra installs it
        raise UrdfImportError("expanding Xacro needs the xacro package: pip install 'rekuest[kulisse]'") from error
    return xacro.process_file(str(path), mappings=dict(arguments or {})).toxml()


def _resolve(filename: str, base: Path, packages: dict[str, Path]) -> Path:
    match = re.fullmatch(r"package://([^/]+)/(.*)", filename)
    if match:
        package, rest = match.groups()
        if package not in packages:
            raise UrdfImportError(f"{filename} names the package {package!r}; say where it is with --package {package}=<directory>")
        return packages[package] / rest
    return (base / filename.removeprefix("file://")).resolve()


def _geometry(element: ET.Element, base: Path, packages: dict[str, Path]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vertices, normals and faces of one visual's geometry, in the visual's frame."""
    import trimesh

    shape = next(iter(element), None)
    if shape is None:
        raise UrdfImportError("a visual has an empty geometry")
    if shape.tag == "box":
        mesh = trimesh.creation.box(extents=_floats(shape.get("size"), 3, (1.0, 1.0, 1.0)))
    elif shape.tag == "cylinder":
        mesh = trimesh.creation.cylinder(radius=float(shape.get("radius", 1)), height=float(shape.get("length", 1)), sections=32)
    elif shape.tag == "sphere":
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=float(shape.get("radius", 1)))
    elif shape.tag == "mesh":
        source = _resolve(shape.get("filename", ""), base, packages)
        if not source.is_file():
            raise UrdfImportError(f"mesh file {source} does not exist")
        mesh = trimesh.load(source, force="mesh", process=False)
        scale = shape.get("scale")
        if scale is not None:
            mesh.apply_scale(_floats(scale, 3, (1.0, 1.0, 1.0)))
    else:
        raise UrdfImportError(f"geometry <{shape.tag}> is not supported")
    return np.asarray(mesh.vertices), np.asarray(mesh.vertex_normals), np.asarray(mesh.faces)


def import_urdf(
    path: str | Path,
    *,
    packages: dict[str, str | Path] | None = None,
    arguments: dict[str, str] | None = None,
) -> bytes:
    """Convert a URDF or Xacro file into the bytes of a GLB model.

    Args:
        path: The ``.urdf`` or ``.urdf.xacro`` file.
        packages: Where each ``package://<name>/`` the file refers to is on disk.
        arguments: Xacro arguments, as ``name: value``.

    Raises:
        UrdfImportError: For a floating or planar joint, a joint and a link sharing a name,
            a robot with no single root link, or geometry that cannot be read.
    """
    path = Path(path)
    roots = {name: Path(directory) for name, directory in (packages or {}).items()}
    text = expand(path, arguments)
    robot = ET.fromstring(text)

    links = {link.get("name", ""): link for link in robot.findall("link")}
    joints = robot.findall("joint")
    named_materials = {material.get("name", ""): material for material in robot.findall("material")}
    for joint in joints:
        name, kind = joint.get("name", ""), joint.get("type", "")
        if kind in ("floating", "planar"):
            raise UrdfImportError(f"joint {name!r} is {kind}; only fixed, revolute, continuous and prismatic joints are supported")
        if kind != "fixed" and kind not in MOVING:
            raise UrdfImportError(f"joint {name!r} has the unknown type {kind!r}")
        if kind in MOVING and name in links:
            raise UrdfImportError(f"the joint and the link {name!r} share a name; a node name must be unique, so rename one of them")
    children_of: dict[str, list[ET.Element]] = {}
    child_links: set[str] = set()
    for joint in joints:
        parent = joint.find("parent")
        child = joint.find("child")
        if parent is None or child is None:
            raise UrdfImportError(f"joint {joint.get('name')!r} needs a parent and a child")
        children_of.setdefault(parent.get("link", ""), []).append(joint)
        child_links.add(child.get("link", ""))
    root_links = [name for name in links if name not in child_links]
    if len(root_links) != 1:
        raise UrdfImportError(f"a robot has one root link; this one has {len(root_links)}: {root_links}")

    builder = Builder()
    table: list[dict[str, Any]] = []

    def material_of(visual: ET.Element) -> int | None:
        material = visual.find("material")
        if material is None:
            return None
        name = material.get("name", "")
        source = material if (material.find("color") is not None or material.find("texture") is not None) else named_materials.get(name)
        if source is None:
            return None
        color = source.find("color")
        texture = source.find("texture")
        rgba = _floats(color.get("rgba"), 4, (1.0, 1.0, 1.0, 1.0)) if color is not None else None
        image = _resolve(texture.get("filename", ""), path.parent, roots) if texture is not None else None
        return builder.material(name or json.dumps([rgba, str(image)]), rgba, image)

    def build_link(name: str, transform: dict[str, Any]) -> int:
        link = links.get(name)
        if link is None:
            raise UrdfImportError(f"a joint refers to the link {name!r}, which does not exist")
        children: list[int] = []
        for index, visual in enumerate(link.findall("visual")):
            geometry = visual.find("geometry")
            if geometry is None:
                continue
            mesh = builder.mesh(*_geometry(geometry, path.parent, roots), material_of(visual))
            children.append(builder.node({"name": f"{name}_visual_{index}", "mesh": mesh, **Origin.parse(visual.find("origin")).node()}))
        for joint in children_of.get(name, []):
            joint_name, kind = joint.get("name", ""), joint.get("type", "")
            origin = Origin.parse(joint.find("origin")).node()
            child = joint.find("child")
            assert child is not None
            if kind == "fixed":
                children.append(build_link(child.get("link", ""), origin))
                continue
            children.append(builder.node({"name": joint_name, **origin, "children": [build_link(child.get("link", ""), {})]}))
            axis_element = joint.find("axis")
            axis = np.array(_floats(axis_element.get("xyz") if axis_element is not None else None, 3, (1.0, 0.0, 0.0)))
            length = float(np.linalg.norm(axis))
            if length == 0:
                raise UrdfImportError(f"joint {joint_name!r} has a zero axis")
            entry: dict[str, Any] = {"name": joint_name, "node": joint_name, "type": kind, "axis": _clean(tuple(axis / length))}
            if kind != "continuous":
                limit = joint.find("limit")
                entry["limit"] = {
                    "lower": float(limit.get("lower", 0)) if limit is not None else 0.0,
                    "upper": float(limit.get("upper", 0)) if limit is not None else 0.0,
                }
            mimic = joint.find("mimic")
            if mimic is not None:
                entry["mimic"] = {"joint": mimic.get("joint", ""), "multiplier": float(mimic.get("multiplier", 1)), "offset": float(mimic.get("offset", 0))}
            table.append(entry)
        node: dict[str, Any] = {"name": name, **transform}
        if children:
            node["children"] = children
        return builder.node(node)

    root = build_link(root_links[0], {})
    # URDF is Z-up, glTF Y-up: the root is turned once, and every frame below it follows.
    upright = (-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))
    existing = tuple(builder.nodes[root].get("rotation", (0.0, 0.0, 0.0, 1.0)))
    builder.nodes[root]["rotation"] = _clean(_multiply(upright, existing))

    # The table is in document order, not in the order the tree was walked.
    order = {joint.get("name", ""): index for index, joint in enumerate(joints)}
    table.sort(key=lambda entry: order[entry["name"]])
    extras = {"arkitekt": {"version": 1, "joints": table, "source": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()}}
    return builder.glb(root, extras)


def with_joints(data: bytes, joints: list[dict[str, Any]]) -> bytes:
    """A copy of a GLB with ``joints`` as its joint table, for models that were not imported from URDF."""
    magic, version, _length = struct.unpack_from("<III", data, 0)
    json_length, json_type = struct.unpack_from("<II", data, 12)
    if magic != 0x46546C67 or version != 2 or json_type != 0x4E4F534A:
        raise UrdfImportError("the file is not a GLB")
    document = json.loads(data[20 : 20 + json_length])
    extras = document.setdefault("extras", {})
    extras["arkitekt"] = {"version": 1, "joints": joints}
    payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    rest = data[20 + json_length :]
    body = struct.pack("<II", len(payload), 0x4E4F534A) + payload + rest
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body
