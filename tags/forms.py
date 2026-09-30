from django import forms
from .models import (
    RedTag, Station, IssueType, Vehicle, VehicleModel, Section, Category, Employee
)


class RedTagForm(forms.ModelForm):
    chassis_no = forms.CharField(max_length=50, label='Chassis No.')
    model = forms.ModelChoiceField(
        queryset=VehicleModel.objects.all(),
        label='Model',
        empty_label='Select an option',
    )
    lot = forms.CharField(max_length=50, label='Lot Number')
    raised_by_employee_no = forms.CharField(max_length=20, label='Raised By Staff ID')
    verified_by_employee_no = forms.CharField(
        max_length=20, required=False, label='Closed By Staff ID'
    )

    class Meta:
        model = RedTag
        fields = [
            'date_raised', 'section', 'model', 'lot', 'chassis_no',
            'issue_description', 'quantity',
            'category', 'issue_type',
            'raised_by_employee_no', 'station', 'status',
            'closing_date', 'verified_by_employee_no', 'remarks',
        ]
        widgets = {
            'date_raised': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'closing_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'issue_description': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'remarks': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['quantity'].required = True
        self.fields['category'].required = True
        self.fields['issue_type'].required = True
        self.fields['station'].required = True
        self.fields['closing_date'].required = False

        placeholder = 'Select an option'
        for name in ('section', 'category', 'issue_type', 'station'):
            if hasattr(self.fields[name], 'empty_label'):
                self.fields[name].empty_label = placeholder

        # Status starts blank; user must choose Pending or Closed
        self.fields['status'].choices = [('', placeholder)] + list(RedTag.STATUS_CHOICES)
        self.fields['status'].required = True
        self.fields['status'].initial = ''
        if not self.is_bound and not (self.instance and self.instance.pk):
            self.initial.setdefault('status', '')

        for name, field in self.fields.items():
            if name in self.Meta.widgets:
                continue
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
                field.widget.attrs.setdefault('class', 'form-select')
            else:
                field.widget.attrs.setdefault('class', 'form-control')

        if self.instance.pk and self.instance.vehicle_id:
            vehicle = self.instance.vehicle
            self.fields['chassis_no'].initial = vehicle.chassis_no
            self.fields['model'].initial = vehicle.model_id
            self.fields['lot'].initial = vehicle.lot
            if self.instance.raised_by_id:
                self.fields['raised_by_employee_no'].initial = self.instance.raised_by.employee_no
            if self.instance.verified_by_id:
                self.fields['verified_by_employee_no'].initial = self.instance.verified_by.employee_no

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

        # Sections from List sheet master table only
        self.fields['section'].queryset = Section.objects.filter(show_in_form=True).order_by('code')

        if 'section' in self.data:
            try:
                section_id = int(self.data.get('section'))
                self.fields['station'].queryset = Station.objects.filter(
                    section_id=section_id, show_in_form=True
                )
            except (ValueError, TypeError):
                self.fields['station'].queryset = Station.objects.none()
        elif self.instance.pk and self.instance.section_id:
            self.fields['station'].queryset = Station.objects.filter(
                section=self.instance.section, show_in_form=True
            )
        else:
            self.fields['station'].queryset = Station.objects.none()

        self.fields['category'].queryset = Category.objects.all()
        self.fields['model'].queryset = VehicleModel.objects.all()

    def _resolve_employee(self, employee_no, field_name):
        employee_no = (employee_no or '').strip()
        if not employee_no:
            return None
        try:
            return Employee.objects.get(employee_no=employee_no, active=True)
        except Employee.DoesNotExist:
            self.add_error(field_name, f'No active staff member found with ID "{employee_no}".')
            return None

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

        section = cleaned_data.get('section')
        station = cleaned_data.get('station')
        if section and station and station.section_id and station.section_id != section.id:
            self.add_error('station', 'Selected station does not belong to the selected section.')

        return cleaned_data

    def save(self, commit=True):
        chassis_no = self.cleaned_data['chassis_no'].strip()
        model = self.cleaned_data['model']
        lot = self.cleaned_data.get('lot') or ''

        vehicle, _ = Vehicle.objects.update_or_create(
            chassis_no=chassis_no,
            defaults={
                'model': model,
                'lot': lot,
            },
        )
        self.instance.vehicle = vehicle
        self.instance.raised_by = self.cleaned_data.get('raised_by')
        self.instance.verified_by = self.cleaned_data.get('verified_by')
        return super().save(commit=commit)


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
