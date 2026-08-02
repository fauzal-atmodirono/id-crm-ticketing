from chatbot.features.chat.option_lists import build_option_list


def test_valid_options_json():
    opts = build_option_list('{"options": ["Inquiry", "Complaint", "Feedback"]}')
    assert opts.options() == ["Inquiry", "Complaint", "Feedback"]
    assert opts.is_valid("Inquiry") is True
    assert opts.is_valid("inquiry") is True  # case-insensitive match
    assert opts.is_valid("Not A Real Type") is False
    assert opts.is_empty() is False


def test_empty_json_yields_empty_list():
    opts = build_option_list("")
    assert opts.is_empty() is True
    assert opts.options() == []
    assert opts.is_valid("anything") is False


def test_malformed_json_yields_empty_list_not_crash():
    opts = build_option_list("{not valid json")
    assert opts.is_empty() is True


def test_non_dict_json_yields_empty_list():
    opts = build_option_list("[1, 2, 3]")
    assert opts.is_empty() is True


def test_missing_options_key_yields_empty_list():
    opts = build_option_list("{}")
    assert opts.is_empty() is True


def test_options_wrong_type_yields_empty_list():
    opts = build_option_list('{"options": "not-a-list"}')
    assert opts.is_empty() is True


def test_non_string_options_are_stringified():
    opts = build_option_list('{"options": [1, "two"]}')
    assert opts.options() == ["1", "two"]
