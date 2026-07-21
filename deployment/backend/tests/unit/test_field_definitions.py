from app.schemas.agent import parse_field_definitions


def test_parse_field_definitions_coerces_minimal_dict_entry():
    definitions = parse_field_definitions(
        [{"name": "email", "type": "email", "required": True}]
    )

    assert len(definitions) == 1
    assert definitions[0].name == "email"
    assert definitions[0].type == "email"
    assert definitions[0].label == "Email"
    assert definitions[0].required is True


def test_parse_field_definitions_falls_back_when_all_entries_invalid():
    definitions = parse_field_definitions([{"type": "email"}, {}, 123])

    assert [definition.name for definition in definitions] == [
        "name",
        "email",
        "phone",
        "date",
        "time",
    ]
