from app.core.config import Settings


def test_blank_secrets_are_treated_as_unset(monkeypatch):
    for name in ("API_KEY", "OPENAI_API_KEY", "GOOGLE_PLACES_API_KEY", "HUBSPOT_ACCESS_TOKEN"):
        monkeypatch.setenv(name, "  ")
    s = Settings(_env_file=None)
    assert s.api_key is None
    assert s.openai_api_key is None
    assert s.google_places_api_key is None
    assert s.hubspot_access_token is None


def test_secrets_are_not_leaked_in_repr(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-very-secret")
    s = Settings(_env_file=None)
    assert s.openai_api_key.get_secret_value() == "sk-very-secret"
    assert "sk-very-secret" not in repr(s)


def test_country_lists_are_parsed(monkeypatch):
    monkeypatch.setenv("TARGET_COUNTRIES_PRIMARY", "gr, cy ,")
    s = Settings(_env_file=None)
    assert s.primary_countries == frozenset({"GR", "CY"})
