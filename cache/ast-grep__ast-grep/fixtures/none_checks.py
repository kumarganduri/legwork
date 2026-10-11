def describe(count, name):
    # Two comparisons below use "== None"; ast-grep should find exactly those.
    if count == None:
        return "no count"
    if name == None:
        return "no name"
    if count is None:
        return "unreachable"
    return f"{name}: {count}"
