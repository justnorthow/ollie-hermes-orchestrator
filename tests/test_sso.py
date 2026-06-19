from src.sso import mint_hia_token


def test_mint_token_has_two_parts():
    tok = mint_hia_token("a@b.co", "secret", ttl=60)
    parts = tok.split(".")
    assert len(parts) == 2 and all(parts)
