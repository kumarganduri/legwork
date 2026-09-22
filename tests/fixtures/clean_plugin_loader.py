"""A benign file using one legitimate dynamic import — the kind of code a
plugin system genuinely needs. Should NOT trip the scanner on its own."""

import importlib


def load_plugin(module_name: str, attr_name: str):
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def run_config_driven_hook(config: dict):
    # A single dynamic getattr call from user config — common, legitimate,
    # and should not alone be enough to block a repo.
    handler_name = config["handler"]
    return getattr(config["target"], handler_name)
