"""The generated GraphQL client surface: fragments, operations, ``RekuestApi``.

``schema.py`` here is turms' output for the ``rekuest`` project, and
``schema.graphql`` / ``project.json`` beside it are what ``rekuest.arkitekt``
ships to downstream code generation. It imports its enums and inputs from
:mod:`rekuest.protocol.schema` rather than defining them again.

Only :mod:`rekuest.client` and :mod:`rekuest.arkitekt` may name this package;
``tests/test_layering.py`` enforces that. An agent needs none of it -- it
registers by connecting.
"""
