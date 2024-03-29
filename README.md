# rekuest

[![codecov](https://codecov.io/gh/jhnnsrs/rekuest/branch/master/graph/badge.svg?token=UGXEA2THBV)](https://codecov.io/gh/jhnnsrs/rekuest)
[![PyPI version](https://badge.fury.io/py/rekuest.svg)](https://pypi.org/project/rekuest/)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://pypi.org/project/rekuest/)
![Maintainer](https://img.shields.io/badge/maintainer-jhnnsrs-blue)
[![PyPI pyversions](https://img.shields.io/pypi/pyversions/rekuest.svg)](https://pypi.python.org/pypi/rekuest/)
[![PyPI status](https://img.shields.io/pypi/status/rekuest.svg)](https://pypi.python.org/pypi/rekuest/)

self-documenting asynchronous scalable RPC based on provisionable untrusted actors

## Idea

rekuest is the python client for the rekuest server, it provides a simple interface both register and provide nodes (functionality)
and call other nodes in async and synchronous manner. Contrary to most RPC services, Rekuest is focussed on providing functionaly on the Client, and is especially tailored for secnarious where apps can be developed to perform tasks on users behalves, therefore requiring fine grained access control.

## Prerequesits

Currently rekuest is only compatible within the context of the arkitekt platform, as it relies on "lok" for the authentication and authorization of users and applications. This is however a temporary limitation and will be removed in the future. You should then be able to use rekuest with any authentication and authorization service.


## Install

```python
pip install arkitekt[rekuest]
```

rekuest is relying heavily on asyncio patters and therefore only supports python 3.7 and above. Its api provides sync and async
interfaces through koil.

> If you are working in image analysis checkout the arkitekt platform that also provides data structures for image analysis (composed in the arkitekt platform)

## Get started


Rekuest enables you to register functions (as nodes) on the rekuest server and call them from anywhere. The
python sdk automatically takes care of serialization and deserialization of the data and provides by inspecting
the typehints of the function.


```python
from arkitekt import easy
from herre import Herre, ClientCredentialsGrant

app = easy("app_name")

@app.rekuest.register()
def rpc_function(x: int, name: str) -> str
    """
    A rpc function that we can
    simple call from anywhere

    """
    print(str)


with app:
  app.rekuest.run()

```

Run example:

This node is now register under the application and signed in user and can be provisioned and called to by other apps. By default users
are only able to asign to their own apps. This can be changed on the rekuest server.

## Usage with complex Datastructures

Rekuest takes care of serialization and documentation of standard python datastructures

- str
- bool
- int
- float
- Enum
- Dict
- List

To increase performance and latency it is not possible to serialize complex python objects like numpy arrays into the messages. These are best transformed into immutable objects on a centrally accessible storage and then only the reference is passed.
Rekuest does not impose any rules on how you handle this storage (see mikro for ideas), it provides however a simple api.

```python

class ComplexStructure:
    id: str # A reference for this structure on central storage

    async def ashrink(self):
        return self.id

    @classmethod
    async def aexpand(cls, value):
        return cls.load_from_server(value)


```

by providing two functions:

- shrink
- expand

You can now use this Structure with simple typehints and arkitekt will automaticall shrink (serialize) and expand (deserialize) the structure on calling.

```python

def complex_call(x: ComplexStrucuture) -> int:
    return x.max()

```


