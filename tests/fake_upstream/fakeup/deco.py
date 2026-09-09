import functools


def tag(label):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return f"{label}:{fn(*args, **kwargs)}"

        return wrapper

    return decorator
