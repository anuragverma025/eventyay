from django.db.models import Count, Q
from django.shortcuts import redirect
from django.template.defaultfilters import timeuntil
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy
from django.views.generic import TemplateView
from django_context_decorator import context
from django_scopes import scopes_disabled

from django.http import Http404

def legacy_orga_event_redirect(request, event):
    from eventyay.base.models import Event
    with scopes_disabled():
        events = Event.objects.filter(slug__iexact=event)
        if events.count() == 1:
            e = events.first()
            url = f"/orga/event/{e.organizer.slug}/{e.slug}/"
            if request.META.get('QUERY_STRING'):
                url += '?' + request.META['QUERY_STRING']
            return redirect(url, permanent=True)
        if events.count() > 1 and request.user.is_authenticated:
            user_events = events.filter(
                Q(organizer__id__in=request.user.teams.values_list('organizer_id', flat=True)) |
                Q(submissions__speakers__in=[request.user])
            ).distinct()
            if user_events.count() == 1:
                e = user_events.first()
                url = f"/orga/event/{e.organizer.slug}/{e.slug}/"
                if request.META.get('QUERY_STRING'):
                    url += '?' + request.META['QUERY_STRING']
                return redirect(url, permanent=True)
        raise Http404()

from eventyay.base.models import SpeakerProfile, Submission, SubmissionStates
from eventyay.base.models.event import Event
from eventyay.base.models.log import LogEntry
from eventyay.base.models.organizer import Organizer
from eventyay.base.settings import is_event_series_creation_enabled, is_meetup_creation_enabled
from eventyay.common.text.phrases import phrases
from eventyay.common.permissions import is_admin_mode_active
from eventyay.common.views.mixins import EventPermissionRequired, PermissionRequired
from eventyay.event.stages import get_stages
from eventyay.orga.views.submission import SubmissionStatsMixin
from eventyay.talk_rules.submission import get_missing_reviews


def start_redirect_view(request):
    with scopes_disabled():
        orga_events = set(request.user.get_events_with_any_permission())
        speaker_events = set(Event.objects.filter(submissions__speakers__in=[request.user]))

    # Users with only one event, in only one role, are redirected to that event
    if len(orga_events | speaker_events) == 1 and not (orga_events and speaker_events):
        if orga_events:
            return redirect(orga_events.pop().orga_urls.base)
        return redirect(speaker_events.pop().urls.user_submissions)

    return redirect(reverse('eventyay_common:dashboard'))


class DashboardEventListView(TemplateView):
    template_name = 'orga/event_list.html'

    @property
    def base_queryset(self):
        return self.request.user.get_events_with_any_permission()

    @cached_property
    def queryset(self):
        if is_admin_mode_active(self.request):
            qs = Event.objects.all()
        else:
            qs = self.base_queryset.annotate(
                submission_count=Count(
                    'submissions',
                    filter=Q(
                        submissions__state__in=[
                            state
                            for state in SubmissionStates.display_values.keys()
                            if state not in (SubmissionStates.DELETED, SubmissionStates.DRAFT)
                        ]
                    ),
                )
            ).order_by('-date_from')
        if search := self.request.GET.get('q'):
            qs = qs.filter(Q(name__icontains=search) | Q(slug__icontains=search))
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_orga_events'] = []
        context['past_orga_events'] = []
        for event in self.queryset:
            if event.date_to >= now():
                context['current_orga_events'].insert(0, event)
            else:
                context['past_orga_events'].append(event)
        context['speaker_events'] = (
            Event.objects.filter(submissions__speakers__in=[self.request.user]).distinct().order_by('-date_from')
        )
        context['event_series_creation_enabled'] = is_event_series_creation_enabled(self.request)
        context['meetup_creation_enabled'] = is_meetup_creation_enabled(self.request)
        return context


class DashboardOrganizerEventListView(PermissionRequired, DashboardEventListView):
    permission_required = 'base.view_organizer'

    def get_permission_object(self):
        return self.request.organizer

    @property
    def base_queryset(self):
        return self.request.organizer.events.all()

    @context
    def hide_speaker_events(self):
        return True


class DashboardOrganizerListView(PermissionRequired, TemplateView):
    template_name = 'orga/organizer/list.html'
    permission_required = 'base.list_organizer'

    def filter_organizer(self, organizer, query):
        name = {'en': organizer.name} if isinstance(organizer.name, str) else organizer.name.data
        name = {'en': name} if isinstance(name, str) else name
        return query in organizer.slug or any(query in value for value in name.values())

    @context
    def organizers(self):
        if self.request.user.is_administrator:
            orgs = Organizer.objects.all()
        else:
            orgs = Organizer.objects.filter(
                pk__in={
                    team.organizer_id for team in self.request.user.teams.filter(can_change_organizer_settings=True)
                }
            )
        orgs = orgs.annotate(
            event_count=Count('events', distinct=True),
            team_count=Count('teams', distinct=True),
        )
        query = self.request.GET.get('q')
        if not query:
            return orgs
        query = query.lower().strip()
        return [org for org in orgs if self.filter_organizer(org, query)]


class EventDashboardView(EventPermissionRequired, SubmissionStatsMixin, TemplateView):
    template_name = 'orga/event/dashboard.html'
    permission_required = 'base.talk_orga_access_event'

    def get_action_required_tiles(self, event, _now, can_change_submissions=False):
        tiles = []

        # 1. Proposals waiting for review (for reviewer)
        is_reviewer = event.teams.filter(members__in=[self.request.user], is_reviewer=True).exists()
        if is_reviewer:
            reviews_missing = get_missing_reviews(event, self.request.user).count()
            if reviews_missing > 0:
                tiles.append({
                    'title': _('Proposals waiting for review'),
                    'description': ngettext_lazy(
                        'Proposal is waiting for your review.',
                        'Proposals are waiting for your review.',
                        reviews_missing,
                    ),
                    'count': reviews_missing,
                    'action_url': event.orga_urls.reviews,
                    'action_label': _('Review proposals'),
                    'color': 'warning',
                })

        # 2. Unconfirmed accepted sessions
        unconfirmed_count = event.submissions.filter(state=SubmissionStates.ACCEPTED).count()
        if unconfirmed_count > 0:
            tiles.append({
                'title': _('Unconfirmed sessions'),
                'description': ngettext_lazy(
                    'Accepted proposal waiting for speaker confirmation.',
                    'Accepted proposals waiting for speaker confirmation.',
                    unconfirmed_count,
                ),
                'count': unconfirmed_count,
                'action_url': event.orga_urls.submissions + f'?state={SubmissionStates.ACCEPTED}',
                'action_label': _('View sessions'),
                'color': 'warning',
            })

        # 3. Unscheduled sessions
        unscheduled_count = 0
        if event.wip_schedule:
            unscheduled_count = (
                event.wip_schedule.talks.filter(
                    submission__state__in=SubmissionStates.accepted_states,
                )
                .filter(Q(start__isnull=True) | Q(room__isnull=True) | Q(room__is_unscheduled=True))
                .count()
            )
        if unscheduled_count > 0:
            tiles.append({
                'title': _('Unscheduled sessions'),
                'description': ngettext_lazy(
                    'Accepted session not yet scheduled with a time and room.',
                    'Accepted sessions not yet scheduled with a time and room.',
                    unscheduled_count,
                ),
                'count': unscheduled_count,
                'action_url': event.orga_urls.schedule,
                'action_label': _('Open schedule'),
                'color': 'warning',
            })

        # 4. Incomplete speaker profiles
        incomplete_speakers_count = (
            SpeakerProfile.objects.filter(
                event=event,
                user__submissions__in=event.submissions.filter(state__in=SubmissionStates.accepted_states),
            )
            .filter(Q(biography__isnull=True) | Q(biography__exact=''))
            .distinct()
            .count()
        )
        if incomplete_speakers_count > 0:
            tiles.append({
                'title': _('Incomplete speaker profiles'),
                'description': ngettext_lazy(
                    'Speaker with missing biography details.',
                    'Speakers with missing biography details.',
                    incomplete_speakers_count,
                ),
                'count': incomplete_speakers_count,
                'action_url': event.orga_urls.speakers + '?role=true',
                'action_label': _('View speakers'),
                'color': 'info',
            })

        # 5. Pending notifications / emails in outbox
        pending_mails_count = event.queued_mails.filter(sent__isnull=True).count()
        if pending_mails_count > 0:
            tiles.append({
                'title': _('Pending emails'),
                'description': ngettext_lazy(
                    'Email queued in outbox waiting to be sent.',
                    'Emails queued in outbox waiting to be sent.',
                    pending_mails_count,
                ),
                'count': pending_mails_count,
                'action_url': event.orga_urls.outbox,
                'action_label': _('View outbox'),
                'color': 'info',
            })

        # 6. Submissions with pending changes
        pending_state_submissions = event.submissions.filter(pending_state__isnull=False).count()
        if pending_state_submissions > 0:
            states = '&'.join(
                [
                    f'state=pending_state__{state}'
                    for state, __ in SubmissionStates.get_choices()
                    if state not in (SubmissionStates.DRAFT, SubmissionStates.DELETED)
                ]
            )
            tiles.append({
                'title': _('Pending submission changes'),
                'description': ngettext_lazy(
                    'Submission with unapplied pending state change.',
                    'Submissions with unapplied pending state changes.',
                    pending_state_submissions,
                ),
                'count': pending_state_submissions,
                'action_url': event.orga_urls.submissions + f'?{states}',
                'action_label': _('View submissions'),
                'color': 'info',
            })

        # 7. Unsubmitted proposal drafts
        if hasattr(event, 'cfp') and event.cfp:
            max_deadline = event.cfp.max_deadline
            if max_deadline and _now < max_deadline and can_change_submissions:
                draft_proposals = Submission.all_objects.filter(
                    state=SubmissionStates.DRAFT, event=event
                ).count()
                if draft_proposals > 0:
                    tiles.append({
                        'title': _('Draft proposals'),
                        'description': ngettext_lazy(
                            'Unsubmitted proposal draft in open CfP.',
                            'Unsubmitted proposal drafts in open CfP.',
                            draft_proposals,
                        ),
                        'count': draft_proposals,
                        'action_url': event.orga_urls.send_drafts_reminder,
                        'action_label': _('Send reminder'),
                        'color': 'info',
                    })

        return tiles

    def get_kpi_tiles(self, event):
        tiles = []

        # 1. Submitted proposals
        submitted_count = event.submissions.filter(state=SubmissionStates.SUBMITTED).count()
        tiles.append({
            'count': submitted_count,
            'label': _('Submitted proposals'),
            'url': event.orga_urls.submissions + f'?state={SubmissionStates.SUBMITTED}',
        })

        # 2. Accepted proposals
        accepted_count = event.submissions.filter(state=SubmissionStates.ACCEPTED).count()
        tiles.append({
            'count': accepted_count,
            'label': _('Accepted proposals'),
            'url': event.orga_urls.submissions + f'?state={SubmissionStates.ACCEPTED}',
        })

        # 3. Confirmed sessions
        confirmed_count = event.submissions.filter(state=SubmissionStates.CONFIRMED).count()
        tiles.append({
            'count': confirmed_count,
            'label': _('Confirmed sessions'),
            'url': event.orga_urls.submissions + f'?state={SubmissionStates.CONFIRMED}',
        })

        # 4. Scheduled sessions with schedule completeness
        scheduled_count = (
            event.current_schedule.scheduled_talks.count()
            if event.current_schedule
            else (
                event.wip_schedule.talks.filter(submission__state=SubmissionStates.CONFIRMED, start__isnull=False).count()
                if event.wip_schedule
                else 0
            )
        )
        total_accepted_confirmed = accepted_count + confirmed_count
        extra_label = None
        if total_accepted_confirmed > 0:
            completeness = round((scheduled_count / total_accepted_confirmed) * 100)
            extra_label = _('Schedule completeness: {percent}%').format(percent=completeness)

        tiles.append({
            'count': scheduled_count,
            'label': _('Scheduled sessions'),
            'extra': extra_label,
            'url': event.orga_urls.schedule,
        })

        # 5. Speakers / Submitters
        speaker_count = event.speakers.count()
        submitter_count = event.submitters.count()
        if speaker_count:
            tiles.append({
                'count': speaker_count,
                'label': _('Speakers'),
                'url': event.orga_urls.speakers + '?role=true',
            })
        else:
            tiles.append({
                'count': submitter_count,
                'label': _('Submitters'),
                'url': event.orga_urls.speakers,
            })

        # 6. Pending reviews (event-wide submitted proposals with 0 reviews)
        pending_reviews_count = (
            event.submissions.filter(state=SubmissionStates.SUBMITTED)
            .annotate(review_count=Count('reviews', filter=Q(reviews__score__isnull=False)))
            .filter(review_count=0)
            .count()
        )
        tiles.append({
            'count': pending_reviews_count,
            'label': _('Pending reviews'),
            'url': event.orga_urls.reviews,
        })

        # 7. Rejected proposals
        rejected_count = event.submissions.filter(state=SubmissionStates.REJECTED).count()
        tiles.append({
            'count': rejected_count,
            'label': _('Rejected proposals'),
            'url': event.orga_urls.submissions + f'?state={SubmissionStates.REJECTED}',
        })

        # 8. Withdrawn proposals
        withdrawn_count = event.submissions.filter(
            state__in=[SubmissionStates.WITHDRAWN, SubmissionStates.CANCELED]
        ).count()
        tiles.append({
            'count': withdrawn_count,
            'label': _('Withdrawn proposals'),
            'url': event.orga_urls.submissions + f'?state={SubmissionStates.WITHDRAWN}&state={SubmissionStates.CANCELED}',
        })

        # 9. Emails sent
        sent_mails_count = event.queued_mails.filter(sent__isnull=False).count()
        tiles.append({
            'count': sent_mails_count,
            'label': _('Emails sent'),
            'url': event.orga_urls.sent_mails,
        })

        return tiles

    def get_info_tiles(self, event, _now):
        tiles = []
        today = _now
        if today < event.date_from:
            days = (event.date_from - today).days
            tiles.append({
                'large': days,
                'small': ngettext_lazy('day until event start', 'days until event start', days),
            })
        elif today > event.date_to:
            days = (today - event.date_from).days
            tiles.append({
                'large': days,
                'small': ngettext_lazy('day since event end', 'days since event end', days),
            })
        elif event.date_to != event.date_from:
            day = (today - event.date_from).days + 1
            tiles.append({
                'large': _('Day {number}').format(number=day),
                'small': _('of {total_days} days').format(total_days=(event.date_to - event.date_from).days + 1),
                'url': event.urls.schedule + f'#{today.isoformat()}',
            })

        if hasattr(event, 'cfp') and event.cfp:
            if event.cfp.is_open and (
                event.talks_published or event.private_testmode_talks_enabled
            ):
                tiles.append({
                    'url': event.cfp.urls.public,
                    'large': phrases.cfp.go_to_cfp,
                })
            max_deadline = event.cfp.max_deadline
            if max_deadline and _now < max_deadline:
                tiles.append({
                    'large': timeuntil(max_deadline),
                    'small': _('until the CfP ends'),
                })

        if event.current_schedule:
            tiles.append({
                'large': event.current_schedule.version,
                'small': _('current schedule'),
                'url': event.urls.schedule,
            })

        return tiles

    @context
    def history(self):
        return LogEntry.objects.filter(event=self.request.event).select_related('user', 'event')[:20]

    def get_context_data(self, **kwargs):
        result = super().get_context_data(**kwargs)
        event = self.request.event
        stages = get_stages(event)
        result['timeline'] = stages.values()
        result['go_to_target'] = 'schedule' if stages['REVIEW']['phase'] == 'done' else 'cfp'
        _now = now()
        can_change_submissions = self.request.user.has_perm('base.orga_update_submission', event)
        result['info_tiles'] = self.get_info_tiles(event, _now)
        result['action_required_tiles'] = self.get_action_required_tiles(
            event, _now, can_change_submissions=can_change_submissions
        )
        result['kpi_tiles'] = self.get_kpi_tiles(event)
        return result
