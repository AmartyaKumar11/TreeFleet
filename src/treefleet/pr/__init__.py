"""Pull requests. Inactive unless [pr] enabled is true."""

from treefleet.pr.config import PrSettings, default_pr, load_pr

__all__ = ["PrSettings", "default_pr", "load_pr"]
