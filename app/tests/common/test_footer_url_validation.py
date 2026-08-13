"""Tests for footer URL validation in GlobalSettingsForm."""

import pytest
from django import forms


@pytest.fixture()
def validate():
    """Return the static _validate_footer_url method."""
    from eventyay.control.forms.global_settings import GlobalSettingsForm
    return GlobalSettingsForm._validate_footer_url


class TestFooterUrlValidator:
    # ------------------------------------------------------------------ safe
    def test_empty_string_passes(self, validate):
        assert validate('') == ''

    def test_none_passes(self, validate):
        assert validate(None) is None

    def test_relative_url_passes(self, validate):
        assert validate('/upcoming') == '/upcoming'

    def test_relative_url_with_path_passes(self, validate):
        assert validate('/some/deep/path') == '/some/deep/path'

    def test_https_url_passes(self, validate):
        assert validate('https://docs.eventyay.com') == 'https://docs.eventyay.com'

    def test_http_url_passes(self, validate):
        assert validate('http://example.com') == 'http://example.com'

    def test_https_url_with_path_passes(self, validate):
        assert validate('https://docs.eventyay.com/guide') == 'https://docs.eventyay.com/guide'

    def test_whitespace_stripped(self, validate):
        assert validate('  /terms  ') == '/terms'

    def test_whitespace_stripped_absolute(self, validate):
        assert validate('  https://example.com  ') == 'https://example.com'

    # ------------------------------------------------------------------ unsafe schemes
    def test_javascript_scheme_rejected(self, validate):
        with pytest.raises(forms.ValidationError):
            validate('javascript:alert(1)')

    def test_javascript_scheme_uppercase_rejected(self, validate):
        with pytest.raises(forms.ValidationError):
            validate('JavaScript:alert(1)')

    def test_data_scheme_rejected(self, validate):
        with pytest.raises(forms.ValidationError):
            validate('data:text/html,<script>alert(1)</script>')

    def test_vbscript_scheme_rejected(self, validate):
        with pytest.raises(forms.ValidationError):
            validate('vbscript:msgbox(1)')

    def test_ftp_scheme_rejected(self, validate):
        with pytest.raises(forms.ValidationError):
            validate('ftp://example.com/file')

    def test_file_scheme_rejected(self, validate):
        with pytest.raises(forms.ValidationError):
            validate('file:///etc/passwd')

    # ------------------------------------------------------------------ malformed relative
    def test_relative_without_leading_slash_rejected(self, validate):
        """A plain word with no scheme and no leading / should be rejected."""
        with pytest.raises(forms.ValidationError):
            validate('terms-of-service')

    def test_relative_without_leading_slash_subdomain_rejected(self, validate):
        """A domain-looking string with no scheme must be rejected (ambiguous)."""
        with pytest.raises(forms.ValidationError):
            validate('example.com/terms')
