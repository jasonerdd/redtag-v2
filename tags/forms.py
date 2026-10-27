from django import forms
from django.db.models import Q
from django.utils import timezone

from .models import (
    RedTag, Station, IssueType, Vehicle, VehicleModel, Section, Category, Employee, Dealer,
    EolCheck, ReasonCode,
)
from .receiving import find_vehicle_by_chassis, normalize_chassis


class RedTagForm(forms.ModelForm):
    chassis_no = forms.CharField(max_length=50, label='Chassis No.')
    model_display = forms.CharField(
        required=False, label='Model', disabled=True,
    )
    lot_display = forms.CharField(
        required=False, label='Lot Number', disabled=True,
    )
    customer_display = forms.CharField(
        required=False, label='Customer', disabled=True,
    )
    raised_by_employee_no = forms.CharField(max_length=20, label='Raised By Staff ID')
    verified_by_employee_no = forms.CharField(
        max_length=20, required=False, label='Closed By Staff ID'
    )
    reason_code = forms.ChoiceField(required=False, label='Reason code')

    class Meta:
        model = RedTag
        fields = [
            'date_raised', 'section', 'chassis_no',
            'model_display', 'lot_display', 'customer_display',
            'issue_kind', 'part_name', 'part_number', 'quantity',
            'issue_description',
            'category', 'issue_type', 'reason_code',
            'raised_by_employee_no', 'station', 'status',
            'closing_date', 'verified_by_employee_no', 'remarks',
        ]
        widgets = {
            'date_raised': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'closing_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'issue_description': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'part_name': forms.TextInput(attrs={'class': 'form-control'}),
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
            'remarks': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['quantity'].required = False
        self.fields['part_name'].required = False
        self.fields['part_number'].required = False
        self.fields['category'].required = True
        self.fields['issue_type'].required = True
        self.fields['reason_code'].required = False
        self.fields['station'].required = True
        self.fields['closing_date'].required = False

        placeholder = 'Select an option'
        reason_names = list(ReasonCode.objects.order_by('name').values_list('name', flat=True))
        current_reason = (self.instance.reason_code or '') if self.instance.pk else ''
        if current_reason and current_reason not in reason_names:
            reason_names.append(current_reason)
        self.fields['reason_code'].choices = [('', placeholder)] + [
            (name, name) for name in reason_names
        ]

        for name in ('section', 'category', 'issue_type', 'station'):
            if hasattr(self.fields[name], 'empty_label'):
                self.fields[name].empty_label = placeholder

        # Status starts blank; user must choose Pending or Closed
        self.fields['status'].choices = [('', placeholder)] + list(RedTag.STATUS_CHOICES)
        self.fields['status'].required = True
        self.fields['status'].initial = ''
        if not self.is_bound and not (self.instance and self.instance.pk):
            self.initial.setdefault('status', '')

        self.fields['issue_kind'].choices = [('', placeholder)] + list(RedTag.ISSUE_KIND_CHOICES)
        self.fields['issue_kind'].label = 'Issue relates to'
        has_part_data = bool(
            self.instance.pk and (
                self.instance.part_name or self.instance.part_number or self.instance.quantity is not None
            )
        )
        if not self.is_bound and self.instance.pk and not self.instance.issue_kind and has_part_data:
            self.initial['issue_kind'] = RedTag.ISSUE_PART
        self.fields['issue_kind'].required = not (
            self.instance.pk and not self.instance.issue_kind and not has_part_data
        )

        for name, field in self.fields.items():
            if name in self.Meta.widgets:
                continue
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs.setdefault('class', 'form-select')
            else:
                field.widget.attrs.setdefault('class', 'form-control')

        for name in ('raised_by_employee_no', 'verified_by_employee_no', 'chassis_no'):
            self.fields[name].widget.attrs['autocomplete'] = 'off'
        for name in ('raised_by_employee_no', 'verified_by_employee_no'):
            self.fields[name].widget.attrs['placeholder'] = 'Type staff ID or name'

        if self.instance.pk and self.instance.vehicle_id:
            vehicle = self.instance.vehicle
            self.fields['chassis_no'].initial = vehicle.chassis_no
            self.fields['model_display'].initial = vehicle.model.name
            self.fields['lot_display'].initial = vehicle.lot
            self.fields['customer_display'].initial = (
                vehicle.dealer.name if vehicle.dealer_id else ''
            )
            if self.instance.raised_by_id:
                self.fields['raised_by_employee_no'].initial = self.instance.raised_by.employee_no
            if self.instance.verified_by_id:
                self.fields['verified_by_employee_no'].initial = self.instance.verified_by.employee_no
        elif self.data.get('chassis_no'):
            vehicle = find_vehicle_by_chassis(self.data.get('chassis_no'))
            if vehicle:
                self.fields['model_display'].initial = vehicle.model.name
                self.fields['lot_display'].initial = vehicle.lot
                self.fields['customer_display'].initial = (
                    vehicle.dealer.name if vehicle.dealer_id else ''
                )

        if 'category' in self.data:
            try:
                category_id = int(self.data.get('category'))
                self.fields['issue_type'].queryset = IssueType.objects.filter(category_id=category_id)
            except (ValueError, TypeError):
                self.fields['issue_type'].queryset = IssueType.objects.none()
        elif self.instance.pk and self.instance.category_id:
            self.fields['issue_type'].queryset = IssueType.objects.filter(category=self.instance.category)
        else:
            self.fields['issue_type'].queryset = IssueType.objects.none()

        # Sections/stations from List sheet master table, plus whatever an
        # imported record already uses so it can still be edited.
        existing_section = self.instance.section_id if self.instance.pk else None
        existing_station = self.instance.station_id if self.instance.pk else None
        self.fields['section'].queryset = Section.objects.filter(
            Q(show_in_form=True) | Q(pk=existing_section)
        ).order_by('code')

        visible_station = Q(show_in_form=True) | Q(pk=existing_station)
        if 'section' in self.data:
            try:
                section_id = int(self.data.get('section'))
                self.fields['station'].queryset = Station.objects.filter(
                    visible_station, section_id=section_id
                )
            except (ValueError, TypeError):
                self.fields['station'].queryset = Station.objects.none()
        elif existing_section:
            self.fields['station'].queryset = Station.objects.filter(
                visible_station, section_id=existing_section
            )
        else:
            self.fields['station'].queryset = Station.objects.none()

        self.fields['category'].queryset = Category.objects.all()

        # Excel-imported records often lack fields that new entries must have;
        # don't block editing (e.g. closing) them over data that was never captured.
        if self.instance.pk:
            instance = self.instance
            if not instance.category_id:
                self.fields['category'].required = False
            if not instance.issue_type_id:
                self.fields['issue_type'].required = False
            if not instance.station_id:
                self.fields['station'].required = False
            if not instance.raised_by_id:
                self.fields['raised_by_employee_no'].required = False

    def _resolve_employee(self, employee_no, field_name):
        employee_no = (employee_no or '').strip()
        if not employee_no:
            return None
        existing = [self.instance.raised_by, self.instance.verified_by] if self.instance.pk else []
        for employee in existing:
            if employee and employee.employee_no == employee_no:
                return employee
        try:
            return Employee.objects.get(employee_no=employee_no, active=True)
        except Employee.DoesNotExist:
            self.add_error(field_name, f'No active staff member found with ID "{employee_no}".')
            return None

    def clean_chassis_no(self):
        raw = (self.cleaned_data.get('chassis_no') or '').strip()
        if not raw:
            raise forms.ValidationError('This field is required.')
        # Keep full value for edit of old imported units; normalize only for new tags
        if self.instance.pk and self.instance.vehicle_id:
            return raw
        return normalize_chassis(raw)

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        closing_date = cleaned_data.get('closing_date')
        date_raised = cleaned_data.get('date_raised')
        category = cleaned_data.get('category')
        issue_type = cleaned_data.get('issue_type')
        remarks = (cleaned_data.get('remarks') or '').strip()
        cleaned_data['remarks'] = remarks

        raised_by = self._resolve_employee(
            cleaned_data.get('raised_by_employee_no'), 'raised_by_employee_no'
        )
        cleaned_data['raised_by'] = raised_by

        verified_by = None
        if status == RedTag.STATUS_CLOSED:
            if not closing_date:
                self.add_error('closing_date', 'Closing date is required when status is Closed.')
            verified_by_no = cleaned_data.get('verified_by_employee_no')
            if not (verified_by_no or '').strip():
                self.add_error(
                    'verified_by_employee_no',
                    'Closed By Staff ID is required when status is Closed.',
                )
            else:
                verified_by = self._resolve_employee(
                    verified_by_no, 'verified_by_employee_no'
                )
            if not remarks:
                self.add_error('remarks', 'Remarks are required when status is Closed.')
        else:
            cleaned_data['closing_date'] = None
            cleaned_data['verified_by_employee_no'] = ''
            cleaned_data['remarks'] = ''

        cleaned_data['verified_by'] = verified_by

        if closing_date and date_raised and closing_date < date_raised:
            self.add_error('closing_date', 'Closing date cannot be before the date raised.')

        if category and issue_type and issue_type.category_id != category.id:
            self.add_error('issue_type', 'Selected issue type does not belong to the selected category.')

        kind = cleaned_data.get('issue_kind')
        if kind == RedTag.ISSUE_PART:
            cleaned_data['part_name'] = (cleaned_data.get('part_name') or '').strip()
            cleaned_data['part_number'] = (cleaned_data.get('part_number') or '').strip()
        elif kind == RedTag.ISSUE_QUALITY:
            cleaned_data['part_name'] = ''
            cleaned_data['part_number'] = ''
            cleaned_data['quantity'] = None
        elif self.instance.pk:
            cleaned_data['part_name'] = self.instance.part_name
            cleaned_data['part_number'] = self.instance.part_number
            cleaned_data['quantity'] = self.instance.quantity

        section = cleaned_data.get('section')
        station = cleaned_data.get('station')
        if section and station and station.section_id and station.section_id != section.id:
            self.add_error('station', 'Selected station does not belong to the selected section.')

        chassis_no = cleaned_data.get('chassis_no')
        vehicle = None
        if chassis_no:
            if self.instance.pk and self.instance.vehicle_id:
                # Editing: keep the existing vehicle unless chassis changed
                if chassis_no == self.instance.vehicle.chassis_no or (
                    normalize_chassis(chassis_no) == normalize_chassis(self.instance.vehicle.chassis_no)
                ):
                    vehicle = self.instance.vehicle
                else:
                    vehicle = find_vehicle_by_chassis(chassis_no)
            else:
                vehicle = find_vehicle_by_chassis(chassis_no)

            if not vehicle:
                self.add_error('chassis_no', 'unit does not exist')
            else:
                cleaned_data['vehicle'] = vehicle
                cleaned_data['model_display'] = vehicle.model.name
                cleaned_data['lot_display'] = vehicle.lot
                cleaned_data['customer_display'] = (
                    vehicle.dealer.name if vehicle.dealer_id else ''
                )

        return cleaned_data

    def save(self, commit=True):
        vehicle = self.cleaned_data.get('vehicle')
        if not vehicle:
            raise forms.ValidationError('Vehicle profile is required.')
        self.instance.vehicle = vehicle
        self.instance.raised_by = self.cleaned_data.get('raised_by')
        self.instance.verified_by = self.cleaned_data.get('verified_by')
        return super().save(commit=commit)


class UnitForm(forms.ModelForm):
    """Single-unit receiving form — fields match the receiving sheet columns."""

    class Meta:
        model = Vehicle
        fields = [
            'model', 'vehicle_type', 'lot',
            'chassis_no', 'engine_no', 'arrival_date',
        ]
        labels = {
            'lot': 'Lot No.',
            'chassis_no': 'Chassis No.',
            'engine_no': 'Engine No.',
            'vehicle_type': 'Vehicle Type',
            'arrival_date': 'CKD Arrival Date',
        }
        widgets = {
            'arrival_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'vehicle_type': forms.TextInput(attrs={'class': 'form-control'}),
            'lot': forms.TextInput(attrs={'class': 'form-control'}),
            'chassis_no': forms.TextInput(attrs={'class': 'form-control'}),
            'engine_no': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholder = 'Select an option'
        self.fields['model'].empty_label = placeholder
        self.fields['model'].queryset = VehicleModel.objects.select_related('dealer').all()
        self.fields['lot'].required = True
        self.fields['arrival_date'].required = True

        for name, field in self.fields.items():
            if name in self.Meta.widgets:
                continue
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs.setdefault('class', 'form-select')
            else:
                field.widget.attrs.setdefault('class', 'form-control')

    def clean_chassis_no(self):
        raw = (self.cleaned_data.get('chassis_no') or '').strip()
        if not raw:
            raise forms.ValidationError('This field is required.')
        chassis = normalize_chassis(raw)
        qs = Vehicle.objects.filter(chassis_no=chassis)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f'Chassis "{chassis}" is already received.')
        # Also block if an older full-VIN unit ends with this short form
        if not self.instance.pk:
            existing = find_vehicle_by_chassis(chassis)
            if existing and existing.chassis_no != chassis:
                raise forms.ValidationError(
                    f'A unit ending in "{chassis}" already exists ({existing.chassis_no}).'
                )
        return chassis

    def clean(self):
        cleaned = super().clean()
        model = cleaned.get('model')
        if model and not model.dealer_id:
            self.add_error(
                'model',
                'This model has no customer linked. Set the customer on the model in Admin first.',
            )
        return cleaned

    def save(self, commit=True, user=None):
        instance = super().save(commit=False)
        model = self.cleaned_data.get('model') or instance.model
        if model is not None:
            instance.dealer = model.dealer
        if user is not None and getattr(user, 'is_authenticated', False):
            instance.received_by = user
        if not instance.received_at:
            instance.received_at = timezone.now()
        if commit:
            instance.save()
        return instance


class EolCheckForm(forms.ModelForm):
    class Meta:
        model = EolCheck
        fields = [
            'wheel_alignment', 'brake_test', 'road_test',
            'shower_test', 'air_conditioning', 'diagnosis',
        ]
        labels = {
            'wheel_alignment': 'Wheel alignment',
            'brake_test': 'Brake test',
            'road_test': 'Road test',
            'shower_test': 'Shower test',
            'air_conditioning': 'Air conditioning',
            'diagnosis': 'Diagnosis',
        }
        widgets = {
            name: forms.CheckboxInput(attrs={'class': 'form-check-input'})
            for name in (
                'wheel_alignment', 'brake_test', 'road_test',
                'shower_test', 'air_conditioning', 'diagnosis',
            )
        }


class UnitFilterForm(forms.Form):
    customer = forms.ModelChoiceField(
        queryset=Dealer.objects.all(),
        required=False,
        empty_label='Select an option',
        label='Customer',
    )
    model = forms.ModelChoiceField(
        queryset=VehicleModel.objects.all(),
        required=False,
        empty_label='Select an option',
    )
    lot = forms.CharField(required=False, label='Lot')
    chassis = forms.CharField(required=False, label='Chassis No.')
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            field.widget.attrs.setdefault('class', css)


class RedTagFilterForm(forms.Form):
    status = forms.ChoiceField(
        choices=[('', 'Select an option')] + list(RedTag.STATUS_CHOICES),
        required=False,
    )
    section = forms.ModelChoiceField(
        queryset=Section.objects.filter(show_in_form=True),
        required=False,
        empty_label='Select an option',
    )
    station = forms.ModelChoiceField(
        queryset=Station.objects.filter(show_in_form=True).select_related('section'),
        required=False,
        empty_label='Select an option',
    )
    model = forms.ModelChoiceField(
        queryset=VehicleModel.objects.all(),
        required=False,
        empty_label='Select an option',
    )
    customer = forms.ModelChoiceField(
        queryset=Dealer.objects.all(),
        required=False,
        empty_label='Select an option',
        label='Customer',
    )
    lot = forms.CharField(required=False, label='Lot')
    chassis = forms.CharField(required=False, label='Chassis No.')
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            field.widget.attrs.setdefault('class', css)

        section_id = self.data.get('section') if self.is_bound else None
        if section_id and section_id.isdigit():
            self.fields['station'].queryset = self.fields['station'].queryset.filter(
                section_id=section_id
            )
