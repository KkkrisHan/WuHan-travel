from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="-")
request_metrics: ContextVar[dict | None] = ContextVar("request_metrics", default=None)


def count_external(name: str) -> None:
    metrics = request_metrics.get()
    if metrics is not None:
        metrics[name] = metrics.get(name, 0) + 1
