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
        assert isinstance(v, str), "Identifier must be a string"
        assert "/" in v
        return v

    def __repr__(self):
        return f"InputArray({self.value})"
