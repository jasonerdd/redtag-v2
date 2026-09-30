from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.urls import reverse_lazy
from django.db.models import Count, Max, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.utils import timezone
from openpyxl import Workbook

from .models import RedTag, Station, IssueType, Employee, Vehicle, AuditLog
from .forms import RedTagForm, RedTagFilterForm
from .audit import log_red_tag_action

import io
import os
from django.http import HttpResponse
from django.core.management import call_command


class VehicleListView(LoginRequiredMixin, ListView):
    """Red Tag History: one row per unit (chassis)."""
    model = Vehicle
    template_name = 'tags/redtag_list.html'
    context_object_name = 'units'
    paginate_by = 25

    def _tag_filter(self):
        tag_q = Q()
        status = self.request.GET.get('status')
        section = self.request.GET.get('section')
        station = self.request.GET.get('station')
        model = self.request.GET.get('model')
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')

        if status:
            tag_q &= Q(red_tags__status=status)
        if section:
            tag_q &= Q(red_tags__section_id=section)
        if station:
            tag_q &= Q(red_tags__station_id=station)
        if model:
            tag_q &= Q(model_id=model)
        if date_from:
            tag_q &= Q(red_tags__date_raised__gte=date_from)
        if date_to:
            tag_q &= Q(red_tags__date_raised__lte=date_to)
        return tag_q

    def get_queryset(self):
        tag_q = self._tag_filter()
        lot = self.request.GET.get('lot')
        chassis = self.request.GET.get('chassis')
        q = self.request.GET.get('q')

        qs = Vehicle.objects.select_related('model', 'dealer')

        if tag_q:
            qs = qs.filter(tag_q)
        else:
            qs = qs.filter(red_tags__isnull=False)

        if lot:
            qs = qs.filter(lot__icontains=lot)
        if chassis:
            qs = qs.filter(chassis_no__icontains=chassis)
        if q:
            qs = qs.filter(
                Q(chassis_no__icontains=q)
                | Q(model__name__icontains=q)
                | Q(lot__icontains=q)
                | Q(red_tags__issue_description__icontains=q)
            )

        qs = qs.distinct().annotate(
            issue_count=Count('red_tags', distinct=True),
            pending_count=Count(
                'red_tags',
                filter=Q(red_tags__status=RedTag.STATUS_PENDING),
                distinct=True,
            ),
            closed_count=Count(
                'red_tags',
                filter=Q(red_tags__status=RedTag.STATUS_CLOSED),
                distinct=True,
            ),
            latest_date=Max('red_tags__date_raised'),
        ).order_by('-latest_date', 'chassis_no')

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = RedTagFilterForm(self.request.GET or None)
        context['query'] = self.request.GET.get('q', '')
        return context


class VehicleDetailView(LoginRequiredMixin, DetailView):
    """All red tag issues for one unit (chassis)."""
    model = Vehicle
    template_name = 'tags/vehicle_detail.html'
    context_object_name = 'unit'

    def get_queryset(self):
        return Vehicle.objects.select_related('model', 'dealer')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['redtags'] = (
            self.object.red_tags.select_related(
                'section', 'station', 'category', 'issue_type',
                'raised_by', 'verified_by',
            )
        )
        return context


class RedTagDetailView(LoginRequiredMixin, DetailView):
    model = RedTag
    template_name = 'tags/redtag_detail.html'
    context_object_name = 'redtag'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['employees'] = Employee.objects.filter(active=True)
        return context


class RedTagCreateView(LoginRequiredMixin, CreateView):
    model = RedTag
    form_class = RedTagForm
    template_name = 'tags/redtag_form.html'
    success_url = reverse_lazy('redtag-create')

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault('date_raised', timezone.localdate())
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        log_red_tag_action(self.request, self.object, AuditLog.ACTION_CREATED)
        messages.success(self.request, 'Red tag saved. You can enter another one below.')
        return response


class RedTagUpdateView(LoginRequiredMixin, UpdateView):
    model = RedTag
    form_class = RedTagForm
    template_name = 'tags/redtag_form.html'

    def get_success_url(self):
        return reverse_lazy('vehicle-detail', kwargs={'pk': self.object.vehicle_id})

    def form_valid(self, form):
        response = super().form_valid(form)
        log_red_tag_action(self.request, self.object, AuditLog.ACTION_UPDATED)
        messages.success(self.request, 'Red tag updated successfully.')
        return response


@login_required
def close_redtag(request, pk):
    redtag = get_object_or_404(RedTag, pk=pk)
    if request.method == 'POST':
        verified_by_id = request.POST.get('verified_by')
        if not verified_by_id:
            messages.error(request, 'Select who verified/cleared this tag.')
            return redirect('redtag-detail', pk=pk)

        redtag.status = RedTag.STATUS_CLOSED
        redtag.closing_date = timezone.localdate()
        redtag.verified_by_id = verified_by_id
        remarks = request.POST.get('remarks', '').strip()
        if remarks:
            redtag.remarks = remarks
        redtag.save()
        log_red_tag_action(request, redtag, AuditLog.ACTION_CLOSED)
        messages.success(request, f'Red tag #{redtag.pk} closed.')
    return redirect('redtag-detail', pk=pk)


@login_required
def export_redtags(request):
    qs = RedTag.objects.select_related(
        'vehicle', 'vehicle__model', 'vehicle__dealer', 'section', 'station',
        'category', 'issue_type', 'raised_by', 'verified_by'
    ).all()

    status = request.GET.get('status')
    section = request.GET.get('section')
    station = request.GET.get('station')
    model = request.GET.get('model')
    lot = request.GET.get('lot')
    chassis = request.GET.get('chassis')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if status:
        qs = qs.filter(status=status)
    if section:
        qs = qs.filter(section_id=section)
    if station:
        qs = qs.filter(station_id=station)
    if model:
        qs = qs.filter(vehicle__model_id=model)
    if lot:
        qs = qs.filter(vehicle__lot__icontains=lot)
    if chassis:
        qs = qs.filter(vehicle__chassis_no__icontains=chassis)
    if date_from:
        qs = qs.filter(date_raised__gte=date_from)
    if date_to:
        qs = qs.filter(date_raised__lte=date_to)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Red Tag'
    headers = [
        'Date', 'Section', 'Dealer', 'Model', 'Lot', 'Chassis no.',
        'Part Number', 'Part Name', 'Qnty', 'Issue Description',
        'Category', 'Issue Type', 'Reason Code', 'Raised By', 'Station',
        'Action', 'Status', 'Closing Date', 'Cleared / Verified By',
        'Remarks', 'Location', 'VIN Size',
    ]
    ws.append(headers)

    for tag in qs.iterator(chunk_size=2000):
        ws.append([
            tag.date_raised,
            tag.section.code if tag.section_id else '',
            tag.vehicle.dealer.name if tag.vehicle.dealer_id else '',
            tag.vehicle.model.name if tag.vehicle.model_id else '',
            tag.vehicle.lot,
            tag.vehicle.chassis_no,
            tag.part_number,
            tag.part_name,
            tag.quantity,
            tag.issue_description,
            tag.category.name if tag.category_id else '',
            tag.issue_type.name if tag.issue_type_id else '',
            tag.reason_code,
            tag.raised_by.employee_no if tag.raised_by_id else '',
            tag.station.name if tag.station_id else '',
            tag.action_taken,
            tag.status,
            tag.closing_date,
            tag.verified_by.employee_no if tag.verified_by_id else '',
            tag.remarks,
            tag.location,
            tag.vehicle.vi_size,
        ])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="red_tag_export.xlsx"'
    wb.save(response)
    return response


def load_stations(request):
    section_id = request.GET.get('section_id')
    if request.GET.get('all') and not section_id:
        stations = Station.objects.filter(show_in_form=True).select_related('section')
        data = [{'id': s.id, 'name': str(s)} for s in stations]
        return JsonResponse(data, safe=False)
    stations = Station.objects.filter(
        section_id=section_id, show_in_form=True
    ).order_by('name')
    data = [{'id': s.id, 'name': s.name} for s in stations]
    return JsonResponse(data, safe=False)


def load_issue_types(request):
    category_id = request.GET.get('category_id')
    issue_types = IssueType.objects.filter(category_id=category_id).order_by('name')
    data = [{'id': i.id, 'name': i.name} for i in issue_types]
    return JsonResponse(data, safe=False)


class AuditLogListView(LoginRequiredMixin, ListView):
    model = AuditLog
    template_name = 'tags/audit_list.html'
    context_object_name = 'logs'
    paginate_by = 50

    def get_queryset(self):
        qs = AuditLog.objects.select_related('user', 'red_tag').all()
        action = self.request.GET.get('action')
        chassis = self.request.GET.get('chassis')
        q = self.request.GET.get('q')
        if action:
            qs = qs.filter(action=action)
        if chassis:
            qs = qs.filter(chassis_no__icontains=chassis)
        if q:
            qs = qs.filter(
                Q(summary__icontains=q)
                | Q(username__icontains=q)
                | Q(chassis_no__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action_choices'] = AuditLog.ACTION_CHOICES
        context['selected_action'] = self.request.GET.get('action', '')
        context['chassis'] = self.request.GET.get('chassis', '')
        context['query'] = self.request.GET.get('q', '')
        return context


class AuditLogDetailView(LoginRequiredMixin, DetailView):
    model = AuditLog
    template_name = 'tags/audit_detail.html'
    context_object_name = 'log'


def bootstrap(request):
    if request.GET.get('key') != os.environ.get('BOOTSTRAP_SECRET'):
        return HttpResponse('Forbidden', status=403)

    output = io.StringIO()
    call_command('migrate', stdout=output)
    call_command('collectstatic', interactive=False, stdout=output)

    from django.contrib.auth import get_user_model
    User = get_user_model()
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser('admin', 'admin@example.com', 'TempPass123!')
        output.write('\nSuperuser "admin" created with password TempPass123! — change this immediately.\n')
    else:
        output.write('\nSuperuser "admin" already exists.\n')

    return HttpResponse(f'<pre>{output.getvalue()}</pre>')