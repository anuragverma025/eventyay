import pytest
from django_scopes import scope

from eventyay.common.forms.fields import RichTextField
from eventyay.common.forms.widgets import RichTextWidget
from eventyay.eventyay_common.forms.event import EventCommonSettingsForm
from eventyay.orga.forms import SubmissionForm
from eventyay.orga.forms.event import ReviewScoreCategoryForm


@pytest.mark.django_db
def test_submissionform_content_locale_choices(event):
    event.locale_array = "en,de"
    event.content_locale_array = "en,de,fr"
    event.save()
    with scope(event=event):
        submission_form = SubmissionForm(event)
        assert submission_form.fields["content_locale"].choices == [
            ("en", "English"),
            ("de", "Deutsch"),
            ("fr", "Français"),
        ]


@pytest.mark.django_db
def test_submissionform_richtext_fields_and_widgets(event):
    with scope(event=event):
        form = SubmissionForm(event)
        for field_name in ('abstract', 'description', 'notes'):
            assert isinstance(form.fields[field_name], RichTextField)
            assert isinstance(form.fields[field_name].widget, RichTextWidget)
        assert form.fields['abstract'].help_text == ''
        assert form.fields['description'].help_text == ''


@pytest.mark.django_db
def test_submissionform_sanitizes_richtext_fields(event):
    with scope(event=event):
        submission_type = event.submission_types.first()
        data = {
            'title': 'Test Proposal',
            'submission_type': submission_type.pk,
            'state': 'submitted',
            'abstract': '<p>Valid abstract</p><script>alert("xss")</script>',
            'description': '<p>Valid description <a href="javascript:alert(1)">link</a></p>',
            'notes': '<p>Notes with <span onclick="evil()">click</span></p>',
            'content_locale': 'en',
            'duration': '',
            'slot_count': 1,
        }
        form = SubmissionForm(event, data=data)
        assert form.is_valid(), form.errors
        cleaned = form.cleaned_data
        assert '<script>' not in cleaned['abstract']
        assert 'alert("xss")' not in cleaned['abstract']
        assert '<p>Valid abstract</p>' in cleaned['abstract']
        assert 'javascript:' not in cleaned['description']
        assert 'onclick' not in cleaned['notes']


@pytest.mark.django_db
def test_submissionform_converts_legacy_markdown_in_initial_data(event, submission):
    with scope(event=event):
        submission.abstract = '**Legacy bold** and *italic* with [a link](https://example.com)'
        submission.description = '- First item\n- Second item'
        submission.notes = 'Simple legacy notes paragraph'
        submission.save()

        form = SubmissionForm(event, instance=submission)
        assert '<strong>Legacy bold</strong>' in form.initial['abstract']
        assert '<em>italic</em>' in form.initial['abstract']
        assert '<a href="https://example.com"' in form.initial['abstract']
        assert '<ul>' in form.initial['description']
        assert '<li>First item</li>' in form.initial['description']
        assert '<p>Simple legacy notes paragraph</p>' in form.initial['notes']


@pytest.mark.django_db
def test_submissionform_preserves_existing_tiptap_html_in_initial_data(event, submission):
    with scope(event=event):
        html_abstract = '<p>Already <strong>HTML</strong> content with <a href="https://example.com">link</a></p>'
        html_description = '<ul><li>Existing point</li></ul>'
        html_notes = '<blockquote><p>Existing note</p></blockquote>'
        submission.abstract = html_abstract
        submission.description = html_description
        submission.notes = html_notes
        submission.save()

        form = SubmissionForm(event, instance=submission)
        assert form.initial['abstract'] == html_abstract
        assert form.initial['description'] == html_description
        assert form.initial['notes'] == html_notes


@pytest.mark.django_db
def test_anonymiseform_converts_legacy_markdown_in_initial_data(event, submission):
    from eventyay.orga.forms import AnonymiseForm

    with scope(event=event):
        submission.abstract = '**Legacy bold**'
        submission.description = '* Bullet'
        submission.notes = 'Legacy notes'
        submission.save()

        form = AnonymiseForm(instance=submission)
        assert '<strong>Legacy bold</strong>' in form.initial['abstract']
        assert '<ul>' in form.initial['description']
        assert '<li>Bullet</li>' in form.initial['description']
        assert '<p>Legacy notes</p>' in form.initial['notes']



def test_event_common_settings_form_has_separate_header_color_controls():
    assert 'header_background_color' in EventCommonSettingsForm.auto_fields
    assert 'header_text_color' in EventCommonSettingsForm.auto_fields
    assert 'navigation_text_color' in EventCommonSettingsForm.auto_fields


def test_event_common_settings_form_includes_date_display_controls():
    assert 'show_date_to' in EventCommonSettingsForm.auto_fields
    assert 'show_times' in EventCommonSettingsForm.auto_fields


@pytest.mark.django_db
def test_review_score_category_form_duplicate_score_validation(event):
    with scope(event=event):
        category = event.score_categories.first()
        scores = list(category.scores.all())

        # Test duplicate values in existing scores
        data_invalid = {
            'name_0': str(category.name),
            'weight': '1',
            f'value_{scores[0].id}': '3',
            f'label_{scores[0].id}': 'Weak',
            f'value_{scores[1].id}': '3',  # Duplicate value 3
            f'label_{scores[1].id}': 'Strong',
            f'value_{scores[2].id}': '5',
            f'label_{scores[2].id}': 'Excellent',
        }
        form_invalid = ReviewScoreCategoryForm(event=event, instance=category, data=data_invalid)
        assert not form_invalid.is_valid()
        assert f'value_{scores[0].id}' not in form_invalid.errors
        assert f'value_{scores[1].id}' in form_invalid.errors
        assert 'Duplicate score values are not allowed' in str(form_invalid.errors[f'value_{scores[1].id}'])

        # Test unique values in existing scores
        data_valid = {
            'name_0': str(category.name),
            'weight': '1',
            f'value_{scores[0].id}': '1',
            f'label_{scores[0].id}': 'Weak',
            f'value_{scores[1].id}': '3',
            f'label_{scores[1].id}': 'Strong',
            f'value_{scores[2].id}': '5',
            f'label_{scores[2].id}': 'Excellent',
        }
        form_valid = ReviewScoreCategoryForm(event=event, instance=category, data=data_valid)
        assert form_valid.is_valid()

