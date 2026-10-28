"""Version-proof route listing for tests. FastAPI >= 0.13x wraps included routers in an `_IncludedRouter` that has no
`.path`, so `app.routes` no longer lists every endpoint. The OpenAPI schema flattens them on every version."""


def route_table(app):
    """{(path, METHOD)} for every documented API route."""
    return {(p, m.upper()) for p, ops in app.openapi()["paths"].items() for m in ops if m.lower() in ("get", "post", "put", "patch", "delete")}


def route_paths(app):
    return set(app.openapi()["paths"])
