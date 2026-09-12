"""PyInstaller entry point.

A top-level launcher (absolute import) so the frozen executable has a proper
package context — running guardian/__main__.py directly would fail its relative
imports. `python -m guardian` still uses guardian/__main__.py.
"""

from guardian.app import main

if __name__ == "__main__":
    main()
