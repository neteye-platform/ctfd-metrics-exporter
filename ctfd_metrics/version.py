"""Single source of truth for the version, imported by the build_info metric.

Kept in its own module so ``ctf.py`` can read it without importing the
package root, which imports ``ctf.py`` in turn.
"""

__version__ = "1.1.0"
