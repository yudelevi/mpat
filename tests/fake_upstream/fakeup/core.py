from fakeup.deco import tag


def greet(name, punct="!"):
    return f"hi {name}{punct}"


async def agreet(name):
    return f"hi {name}"


def count(n):
    yield from range(n)


async def acount(n):
    for i in range(n):
        yield i


@tag("v1")
def decorated():
    return "decorated"


class Store:
    limit = 3

    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)
        return len(self.items)

    @staticmethod
    def double(x):
        return x * 2

    @classmethod
    def build(cls):
        return cls()

    @property
    def size(self):
        return len(self.items)


try:
    import json  # noqa: F401

    def guarded():
        return "guarded"
except ImportError:
    pass


class Base:
    def go(self):
        return "base"


class Child(Base):
    pass


class Other(Base):
    pass


if False:

    def twice():
        return "dead"

else:

    def twice():
        return "live"


if False:

    @tag("dead")
    def thrice():
        return "dead"

else:

    @tag("live")
    def thrice():
        return "live"
