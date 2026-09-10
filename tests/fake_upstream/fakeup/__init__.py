from fakeup.core import greet as greet_alias

__all__ = ["greet_alias", "DEBUG", "LIMIT", "REGISTRY"]

DEBUG = False
LIMIT = 16
REGISTRY = {"plain": 1}


def __getattr__(name: str):
    if name == "lazy_greet":
        from fakeup.core import greet

        return greet
    raise AttributeError(name)
