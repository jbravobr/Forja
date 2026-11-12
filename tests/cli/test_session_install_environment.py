from forja.core.isolation.environment import install_environment


def test_the_installer_gets_what_pip_needs_and_no_keys_or_tokens() -> None:
    given = {
        "PATH": "/bin",
        "https_proxy": "http://p",
        "PIP_INDEX_URL": "https://i",
        "ANTHROPIC_API_KEY": "secret",
        "GITHUB_TOKEN": "secret",
    }

    assert install_environment(given) == {
        "PATH": "/bin",
        "https_proxy": "http://p",
        "PIP_INDEX_URL": "https://i",
    }
