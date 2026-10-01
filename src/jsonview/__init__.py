"""A lazy-loading JSON viewer for Windows 11.

From Python::

    import jsonview
    jsonview.view(data)                 # any dict/list
    jsonview.view("points.json")        # a file
    jsonview.view(requests.get(url))    # anything with a .json() method
"""

__version__ = "0.1.0"

from .api import view

__all__ = ["view", "__version__"]
