from datetime import date


class QString(str):
    pass


class Identifier(str):
    @classmethod
    def __get_validators__(cls):
        # one or more validators may be yielded which will be called in the
        # order to validate the input, each validator will receive as an input
        # the value returned from the previous validator
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not isinstance(v, str): raise TypeError("Identifier must be a string")
        if not "/" in v: raise ValueError("Identifier must be a path")
        return v

    def __repr__(self):
        return f"InputArray({self.value})"
