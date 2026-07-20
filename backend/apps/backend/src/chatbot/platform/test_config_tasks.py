from chatbot.platform.config import Settings


def test_tasks_reminder_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.tasks_reminder_warning_minutes == 60
    assert s.tasks_reminder_whatsapp_enabled is False


def test_tasks_reminder_env_override() -> None:
    s = Settings(
        _env_file=None,
        tasks_reminder_warning_minutes=30,
        tasks_reminder_whatsapp_enabled=True,
    )
    assert s.tasks_reminder_warning_minutes == 30
    assert s.tasks_reminder_whatsapp_enabled is True
