from chatbot.features.chat.phone.caller_tools import LOOKUP_CALLER_TOOL, SAVE_CALLER_DETAILS_TOOL


def test_caller_tools_declare_names_and_parameters() -> None:
    save = SAVE_CALLER_DETAILS_TOOL.function_declarations[0]
    assert save.name == "save_caller_details"
    assert set(save.parameters.properties) == {
        "name",
        "vehicle_registration",
        "vehicle_model",
        "dealer",
    }
    assert not save.parameters.required  # every field is optional
    lookup = LOOKUP_CALLER_TOOL.function_declarations[0]
    assert lookup.name == "lookup_caller"
    assert lookup.parameters.required == ["mobile_number"]
