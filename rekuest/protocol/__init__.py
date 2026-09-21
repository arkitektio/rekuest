"""The rekuest wire vocabulary: what an app speaks, with no client attached.

``schema.py`` here is generated (turms project ``rekuest_protocol``) and holds
every input type and enum in the schema, woven with the hand-written mixins in
:mod:`rekuest.traits`. It is the half of the old ``rekuest.api.schema`` that a
definition, a port, a widget, a state or a blok is described with.

The other half -- the fragments, the operations and the ``RekuestApi`` mixin --
stayed in :mod:`rekuest.api.schema`, which imports its enums and inputs from
here. Only :mod:`rekuest.client` and :mod:`rekuest.arkitekt` may name it.

``types.py`` is hand-written: the function-shape protocols an app's callables
are checked against. Everything else in this package belongs to turms.
"""
