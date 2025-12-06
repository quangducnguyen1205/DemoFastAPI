def test_reader_is_readonly():
    from app.services.semantic_index import reader

    exported = dir(reader)
    forbidden = [name for name in exported if ("save" in name.lower()) or ("write" in name.lower())]
    assert not forbidden, f"reader exposes write-like functions: {forbidden}"

    # Ensure expected read/search funcs exist
    assert hasattr(reader, "load_index_if_exists")
    assert hasattr(reader, "search_vector")
