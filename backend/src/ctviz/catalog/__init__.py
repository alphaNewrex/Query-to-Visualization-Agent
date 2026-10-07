"""The field catalogue: everything the engine knows about a field."""

from ctviz.catalog.entries import ENTRIES
from ctviz.catalog.fields import CATALOG

# `fields.py` declares the types and the empty `CATALOG`; the entries need those types, so they are
# registered here, which runs before any module of the package is used.
CATALOG.update(ENTRIES)
