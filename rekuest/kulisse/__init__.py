"""kulisse: authoring device models for Arkitekt's 3D view.

A model is a plain GLB. This package turns a URDF or Xacro into one, checks any GLB
against the rules a model is held to, and writes the blok snippet that shows it. It
uploads nothing itself: an app registers the file as an asset
(``app.register_asset("scope", "scope.glb")``), names it in a blok
(``@self.assets.scope``) and uploads it when it runs.

Installed with ``pip install "rekuest[kulisse]"``; also a command, ``kulisse``.
"""

from pathlib import Path

from .urdf import UrdfImportError, import_urdf, with_joints
from .validate import ModelPolicy, ModelRefused, ModelReport, model_ref, validate_model

__all__ = [
    "ModelPolicy",
    "ModelRefused",
    "ModelReport",
    "UrdfImportError",
    "check",
    "import_urdf",
    "model_ref",
    "reference",
    "snippet",
    "validate_model",
    "with_joints",
]


def reference(path: str | Path) -> str:
    """The reference of the model file at ``path``: the hash a blok decorating it names as ``for_model``."""
    return model_ref(Path(path).read_bytes())


def check(path: str | Path, policy: ModelPolicy | None = None) -> ModelReport:
    """Hold the model file at ``path`` to the rules; what it contains, or ``ModelRefused``."""
    data = Path(path).read_bytes()
    return validate_model(data, model_ref(data), policy)


def snippet(path: str | Path, name: str | None = None) -> str:
    """The blok component that places the model at ``path``, registered as the asset ``name``.

    The name is the file's, without its extension, when none is given: what the app
    passes to ``app.register_asset``.
    """
    name = name or Path(path).stem
    if not name.isidentifier():
        raise ValueError(f"an asset's name is an identifier: {name!r} is not one, pass a name")
    report = check(path)
    if report.joints:
        return f'<Robot src="@self.assets.{name}" joints="@self.State.joints" />'
    return f'<Model src="@self.assets.{name}" />'
