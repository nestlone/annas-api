"""Public Python interface for annas-api."""

__version__ = "0.0.1"
__author__ = "nestlone"
__email__ = "nestlone@mail.nestlone.com"

from .engine import download_book, probe_book, search_books

__all__ = [
    "__version__",
    "search_books",
    "probe_book",
    "download_book",
]
