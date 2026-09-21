"""Everything rekuest does over GraphQL.

The client layer: the only place in the package that names a fragment, an
operation or the generated ``RekuestApi`` mixin. Everything else -- the agent,
the actors, the definition and structure machinery -- speaks the protocol
vocabulary alone and never imports from here.

An agent does not: it registers by connecting, and nothing in its path calls
the rekuest GraphQL API. ``rekuest.arkitekt`` is the single module that wires
both halves together.
"""
