"""One quiet retained-news snapshot coordinated with existing publishers."""
from contextlib import contextmanager
from ..stores import acquire_write_session, quiet_immutable_read_connection


@contextmanager
def news_read_connection(store_map, *, expected_anchor="store_metadata", checkpoint=None):
    if checkpoint is not None:
        checkpoint()
    # Publishers hold this same physical lock only for their short publication.
    # Keep the descriptor, sidecar and role checks of the immutable gateway.
    with acquire_write_session(store_map, ("news",), timeout_seconds=1):
        with quiet_immutable_read_connection(
            store_map, "news", expected_anchor=expected_anchor
        ) as connection:
            interrupted = []
            def check():
                try:
                    if checkpoint is not None:
                        checkpoint()
                    return 0
                except Exception as exc:
                    interrupted.append(exc)
                    return 1
            connection.set_progress_handler(check, 1000)
            try:
                yield connection
            finally:
                connection.set_progress_handler(None, 0)
                if interrupted:
                    raise interrupted[0]
    if checkpoint is not None:
        checkpoint()
