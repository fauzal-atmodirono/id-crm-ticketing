from app.config import Settings


def test_sop_completion_defaults():
    s = Settings()
    assert s.lifecycle_category_labels == ""
    assert s.email_autoack_enabled is False
    # Template default is the SOP email acknowledgement; check a stable anchor.
    assert "acknowledge receipt of your enquiry" in s.email_autoack_template
    assert "1300 888 877" in s.email_autoack_template
