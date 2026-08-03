from app.services.option_lists import build_option_list


def test_valid_options_json():
    opts = build_option_list('{"options": ["Inquiry", "Complaint", "Feedback"]}')
    assert opts.options() == ["Inquiry", "Complaint", "Feedback"]
    assert opts.is_valid("Complaint") is True
    assert opts.is_valid("Nope") is False


def test_empty_json_yields_empty_list():
    opts = build_option_list("")
    assert opts.is_empty() is True


def test_malformed_json_yields_empty_list_not_crash():
    opts = build_option_list("{broken")
    assert opts.is_empty() is True
