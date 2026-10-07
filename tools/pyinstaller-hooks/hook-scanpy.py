"""Scanpy builds plotting signatures with inspect.getsource during module import."""

# Keep source beside bytecode so FlowSOM's transitive import works in a frozen engine.
module_collection_mode = "pyz+py"
