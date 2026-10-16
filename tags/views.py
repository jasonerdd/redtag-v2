from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView, TemplateView
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models import Count, Max, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.utils import timezone
from openpyxl import Workbook

from .models import RedTag, Station, IssueType, Employee, Vehicle, AuditLog
from .forms import RedTagForm, RedTagFilterForm, UnitForm, UnitFilterForm
from .audit import log_red_tag_action
from .receiving import (
    ParsedRow,
    apply_receiving_rows,
    build_template_workbook,
    find_vehicle_by_chassis,
    normalize_chassis,
    parse_receiving_workbook,
)

import io
import os
from django.http import HttpResponse
from django.core.management import call_command
from django.views.decorators.http import require_GET


class DepartmentLandingView(LoginRequiredMixin, TemplateView):
    template_name = 'tags/landing.html'


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
        customer = self.request.GET.get('customer')
        lot = self.request.GET.get('lot')
        chassis = self.request.GET.get('chassis')
        q = self.request.GET.get('q')

        qs = Vehicle.objects.select_related('model', 'dealer')

        if tag_q:
            qs = qs.filter(tag_q)
        else:
            qs = qs.filter(red_tags__isnull=False)

        if customer:
            qs = qs.filter(dealer_id=customer)
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
    customer = request.GET.get('customer')
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
    if customer:
        qs = qs.filter(vehicle__dealer_id=customer)
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
    include = request.GET.get('include')
    visible = Q(show_in_form=True)
    if include and include.isdigit():
        visible |= Q(pk=include)
    stations = Station.objects.filter(visible, section_id=section_id).order_by('name')
    data = [{'id': s.id, 'name': s.name} for s in stations]
    return JsonResponse(data, safe=False)


def load_issue_types(request):
    category_id = request.GET.get('category_id')
    issue_types = IssueType.objects.filter(category_id=category_id).order_by('name')
    data = [{'id': i.id, 'name': i.name} for i in issue_types]
    return JsonResponse(data, safe=False)


@login_required
@require_GET
def unit_lookup(request):
    chassis = request.GET.get('chassis', '')
    vehicle = find_vehicle_by_chassis(chassis)
    if not vehicle:
        return JsonResponse({'found': False, 'chassis': normalize_chassis(chassis)})
    return JsonResponse({
        'found': True,
        'id': vehicle.id,
        'chassis_no': vehicle.chassis_no,
        'model': vehicle.model.name,
        'model_id': vehicle.model_id,
        'lot': vehicle.lot or '',
        'customer': vehicle.dealer.name if vehicle.dealer_id else '',
        'customer_id': vehicle.dealer_id,
        'vehicle_type': vehicle.vehicle_type or '',
        'engine_no': vehicle.engine_no or '',
    })


@login_required
@require_GET
def chassis_suggest(request):
    q = (request.GET.get('q') or '').strip()
    if len(q) < 3:
        return JsonResponse({'results': []})
    qs = (
        Vehicle.objects.filter(chassis_no__icontains=q)
        .select_related('model', 'dealer')
        .order_by('chassis_no')[:20]
    )
    results = [{
        'chassis_no': v.chassis_no,
        'model': v.model.name,
        'lot': v.lot or '',
        'customer': v.dealer.name if v.dealer_id else '',
    } for v in qs]
    return JsonResponse({'results': results})


class ReceivedUnitListView(LoginRequiredMixin, ListView):
    model = Vehicle
    template_name = 'tags/unit_list.html'
    context_object_name = 'units'
    paginate_by = 25

    def get_queryset(self):
        qs = Vehicle.objects.select_related('model', 'dealer').all()
        customer = self.request.GET.get('customer')
        model = self.request.GET.get('model')
        lot = self.request.GET.get('lot')
        chassis = self.request.GET.get('chassis')
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')

        if customer:
            qs = qs.filter(dealer_id=customer)
        if model:
            qs = qs.filter(model_id=model)
        if lot:
            qs = qs.filter(lot__icontains=lot)
        if chassis:
            qs = qs.filter(chassis_no__icontains=chassis)
        if date_from:
            qs = qs.filter(arrival_date__gte=date_from)
        if date_to:
            qs = qs.filter(arrival_date__lte=date_to)

        # Prefer units that have been through receiving (have arrival_date or received_at)
        return qs.order_by('-arrival_date', '-received_at', '-id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = UnitFilterForm(self.request.GET or None)
        return context


class UnitCreateView(LoginRequiredMixin, CreateView):
    model = Vehicle
    form_class = UnitForm
    template_name = 'tags/unit_form.html'
    success_url = reverse_lazy('unit-list')

    def form_valid(self, form):
        self.object = form.save(user=self.request.user)
        messages.success(
            self.request,
            f'Unit {self.object.chassis_no} received successfully.',
        )
        return redirect(self.get_success_url())


class UnitUpdateView(LoginRequiredMixin, UpdateView):
    model = Vehicle
    form_class = UnitForm
    template_name = 'tags/unit_form.html'

    def get_success_url(self):
        return reverse('vehicle-detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        self.object = form.save(user=self.request.user)
        messages.success(self.request, f'Unit {self.object.chassis_no} updated.')
        return redirect(self.get_success_url())


@login_required
def receiving_upload(request):
    if request.method == 'POST':
        upload = request.FILES.get('file')
        if not upload:
            messages.error(request, 'Choose an Excel file to upload.')
            return redirect('receiving-upload')
        if not upload.name.lower().endswith(('.xlsx', '.xlsm')):
            messages.error(request, 'Upload an .xlsx Excel file.')
            return redirect('receiving-upload')
        try:
            rows = parse_receiving_workbook(upload)
        except Exception as exc:
            messages.error(request, f'Could not read the file: {exc}')
            return redirect('receiving-upload')

        request.session['receiving_preview'] = [row.to_dict() for row in rows]
        return redirect('receiving-preview')

    return render(request, 'tags/receiving_upload.html')


@login_required
def receiving_preview(request):
    raw_rows = request.session.get('receiving_preview')
    if not raw_rows:
        messages.warning(request, 'Upload a receiving sheet first.')
        return redirect('receiving-upload')

    rows = [ParsedRow.from_dict(r) for r in raw_rows]
    counts = {
        'new': sum(1 for r in rows if r.status == 'new'),
        'update': sum(1 for r in rows if r.status == 'update'),
        'error': sum(1 for r in rows if r.status == 'error'),
        'new_customers': sum(1 for r in rows if r.new_customer and r.status != 'error'),
    }

    if request.method == 'POST':
        if 'cancel' in request.POST:
            request.session.pop('receiving_preview', None)
            messages.info(request, 'Upload cancelled.')
            return redirect('receiving-upload')

        result = apply_receiving_rows(rows, user=request.user)
        request.session.pop('receiving_preview', None)
        messages.success(
            request,
            f"Receiving import complete: {result['created']} created, "
            f"{result['updated']} updated, {result['skipped']} skipped.",
        )
        return redirect('unit-list')

    return render(request, 'tags/receiving_preview.html', {
        'rows': rows,
        'counts': counts,
    })


@login_required
@require_GET
def receiving_template(request):
    wb = build_template_workbook()
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="receiving_template.xlsx"'
    wb.save(response)
    return response


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


def csrf_failure(request, reason=''):
    """Recover from stale CSRF tokens (e.g. tabs opened before a login/logout elsewhere)."""
    if request.path == reverse('logout'):
        logout(request)
        return redirect(settings.LOGOUT_REDIRECT_URL)

    messages.warning(
        request,
        'Your session changed (possibly in another tab). Please try again.',
    )
    referer = request.META.get('HTTP_REFERER')
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(referer)
    return redirect(settings.LOGIN_REDIRECT_URL)


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