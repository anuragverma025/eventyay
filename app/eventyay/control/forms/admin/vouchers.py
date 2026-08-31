from django import forms
from django.utils.translation import gettext_lazy as _
from django_scopes import scopes_disabled

from eventyay.base.forms import I18nModelForm
from eventyay.base.forms.widgets import SplitDateTimePickerWidget
from eventyay.base.models import Event, Organizer
from eventyay.base.models.vouchers import InvoiceVoucher, Voucher
from eventyay.control.forms import SplitDateTimeField


class InvoiceVoucherForm(I18nModelForm):
    event_effect = forms.ModelMultipleChoiceField(
        queryset=Event.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label=_('Event effect'),
        help_text=_('The voucher will only be valid for the selected events.'),
    )
    organizer_effect = forms.ModelMultipleChoiceField(
        queryset=Organizer.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label=_('Organizer effect'),
        help_text=_('The voucher will be valid for all events under the selected organizers.'),
    )

    class Meta:
        model = InvoiceVoucher
        localized_fields = '__all__'
        fields = [
            'code',
            'valid_until',
            'value',
            'max_usages',
            'price_mode',
            'budget',
            'event_effect',
            'organizer_effect',
        ]
        field_classes = {
            'valid_until': SplitDateTimeField,
        }
        widgets = {
            'valid_until': SplitDateTimePickerWidget(),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        super().__init__(*args, **kwargs)
        if instance:
            self.fields['event_effect'].initial = instance.limit_events.all()
            self.fields['organizer_effect'].initial = instance.limit_organizer.all()
        with scopes_disabled():
            self.fields['event_effect'].queryset = Event.objects.all()
            self.fields['organizer_effect'].queryset = Organizer.objects.all()

    def clean(self):
        data = super().clean()
        Voucher.clean_value_and_budget(data)
        return data

    def _post_clean(self):
        super()._post_clean()
        for field, error_list in list(self._errors.items()):
            seen = set()
            unique_errors = []
            for err in error_list:
                msg = str(err)
                if msg not in seen:
                    seen.add(msg)
                    unique_errors.append(err)
            self._errors[field] = self.error_class(unique_errors, renderer=self.renderer)

    def save(self, commit=True):
        instance = super().save(commit=False)

        if commit:
            instance.save()

        instance.limit_events.set(self.cleaned_data.get('event_effect', []))
        instance.limit_organizer.set(self.cleaned_data.get('organizer_effect', []))

        if commit:
            self.save_m2m()

        return instance
