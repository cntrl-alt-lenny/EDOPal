"""Stage a new list or release asset with its destination's normal file permissions."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


@contextmanager
def staged_sibling(destination: Path) -> Iterator[Path]:
    path = destination.with_name(f".format-{uuid4().hex}.tmp")
    # Exclusive normal creation inherits the parent ACL and the process umask.
    # tempfile's private access mode is unsuitable for a file we publish by rename.
    with path.open("xb"):
        pass
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
