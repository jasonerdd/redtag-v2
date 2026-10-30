from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import RedTagForm
from .models import (
    AuditLog, Category, Dealer, Employee, IssueType, RedTag, Section, Station, Vehicle,
    VehicleModel,
)
from .receiving import ParsedRow, apply_receiving_rows, find_vehicle_by_chassis


class RedTagRuleTests(TestCase):
    def setUp(self):
        self.dealer = Dealer.objects.create(name='Acme')
        self.model = VehicleModel.objects.create(name='Ranger', dealer=self.dealer)
        self.section = Section.objects.create(code='TRIM', show_in_form=True)
        self.station = Station.objects.create(
            section=self.section, name='Door', show_in_form=True,
        )
        self.category = Category.objects.create(name='Fit')
        self.issue_type = IssueType.objects.create(category=self.category, name='Gap')
        self.employee = Employee.objects.create(
            employee_no='E100', full_name='Ada', active=True,
        )
        self.vehicle = Vehicle.objects.create(
            chassis_no='ABC1234',
            model=self.model,
            dealer=self.dealer,
            lot='L1',
        )
        self.user = get_user_model().objects.create_user('closer', password='secret')

    def payload(self, **overrides):
        data = {
            'date_raised': timezone.localdate().isoformat(),
            'section': self.section.pk,
            'station': self.station.pk,
            'chassis_no': self.vehicle.chassis_no,
            'issue_kind': RedTag.ISSUE_PART,
            'part_name': '',
            'part_number': '',
            'quantity': '',
            'issue_description': 'Paint chip',
            'category': self.category.pk,
            'issue_type': self.issue_type.pk,
            'reason_code': '',
            'raised_by_employee_no': self.employee.employee_no,
            'status': RedTag.STATUS_PENDING,
            'closing_date': '',
            'verified_by_employee_no': '',
            'remarks': '',
        }
        data.update(overrides)
        return data

    def make_tag(self, **overrides):
        values = {
            'date_raised': timezone.localdate(),
            'vehicle': self.vehicle,
            'section': self.section,
            'station': self.station,
            'issue_kind': RedTag.ISSUE_PART,
            'part_name': 'Bolt',
            'part_number': 'B-1',
            'quantity': 2,
            'issue_description': 'Scratch',
            'category': self.category,
            'issue_type': self.issue_type,
            'raised_by': self.employee,
            'status': RedTag.STATUS_PENDING,
        }
        values.update(overrides)
        return RedTag.objects.create(**values)

    def test_unknown_chassis_is_rejected(self):
        form = RedTagForm(self.payload(chassis_no='ZZZ9999'))
        self.assertFalse(form.is_valid())
        self.assertIn('unit does not exist', form.errors['chassis_no'])
        self.assertFalse(RedTag.objects.exists())

    def test_short_suffix_does_not_attach_another_unit(self):
        other = Vehicle.objects.create(
            chassis_no='XBC1234',
            model=self.model,
            dealer=self.dealer,
            lot='L2',
        )

        self.assertEqual(find_vehicle_by_chassis('ABC1234'), self.vehicle)
        self.assertEqual(find_vehicle_by_chassis('XBC1234'), other)
        self.assertIsNone(find_vehicle_by_chassis('BC1234'))
        self.assertIsNone(find_vehicle_by_chassis('C1234'))

        form = RedTagForm(self.payload(chassis_no='BC1234'))
        self.assertFalse(form.is_valid())
        self.assertIn('unit does not exist', form.errors['chassis_no'])
        self.assertFalse(RedTag.objects.exists())

        matched = RedTagForm(self.payload(chassis_no='ABC1234'))
        self.assertTrue(matched.is_valid(), matched.errors)
        saved = matched.save()
        self.assertEqual(saved.vehicle, self.vehicle)
        self.assertNotEqual(saved.vehicle, other)

    def test_exact_short_chassis_beats_a_longer_vin(self):
        legacy = Vehicle.objects.create(
            chassis_no='LEGACY1ABC1234',
            model=self.model,
            dealer=self.dealer,
            lot='L9',
        )
        self.assertEqual(find_vehicle_by_chassis('ABC1234'), self.vehicle)
        self.assertNotEqual(find_vehicle_by_chassis('ABC1234'), legacy)

        self.vehicle.delete()
        self.assertEqual(find_vehicle_by_chassis('ABC1234'), legacy)

    def test_close_requires_a_real_staff_id(self):
        inactive = Employee.objects.create(
            employee_no='E200', full_name='Gone', active=False,
        )
        today = timezone.localdate().isoformat()

        missing = RedTagForm(self.payload(
            status=RedTag.STATUS_CLOSED,
            closing_date=today,
            verified_by_employee_no='',
            remarks='Fixed',
        ))
        self.assertFalse(missing.is_valid())
        self.assertIn('verified_by_employee_no', missing.errors)

        unknown = RedTagForm(self.payload(
            status=RedTag.STATUS_CLOSED,
            closing_date=today,
            verified_by_employee_no='NOBODY',
            remarks='Fixed',
        ))
        self.assertFalse(unknown.is_valid())
        self.assertIn('verified_by_employee_no', unknown.errors)

        inactive_form = RedTagForm(self.payload(
            status=RedTag.STATUS_CLOSED,
            closing_date=today,
            verified_by_employee_no=inactive.employee_no,
            remarks='Fixed',
        ))
        self.assertFalse(inactive_form.is_valid())
        self.assertIn('verified_by_employee_no', inactive_form.errors)

        closed = RedTagForm(self.payload(
            status=RedTag.STATUS_CLOSED,
            closing_date=today,
            verified_by_employee_no=self.employee.employee_no,
            remarks='Fixed',
        ))
        self.assertTrue(closed.is_valid(), closed.errors)
        tag = closed.save()
        self.assertEqual(tag.status, RedTag.STATUS_CLOSED)
        self.assertEqual(tag.verified_by, self.employee)

    def test_close_view_rejects_unknown_staff_and_accepts_active_staff(self):
        tag = self.make_tag()
        self.client.force_login(self.user)
        url = reverse('redtag-close', args=[tag.pk])
        unit_url = reverse('vehicle-detail', args=[self.vehicle.pk])

        page = self.client.get(unit_url)
        self.assertContains(page, 'verified_by_employee_no')
        self.assertContains(page, 'Mark as Closed')

        bogus = self.client.post(url, {
            'verified_by_employee_no': 'NOPE',
            'next': 'https://evil.example/phish',
        })
        self.assertRedirects(bogus, reverse('redtag-detail', args=[tag.pk]))
        tag.refresh_from_db()
        self.assertEqual(tag.status, RedTag.STATUS_PENDING)
        self.assertIsNone(tag.verified_by_id)

        blank = self.client.post(url, {
            'verified_by_employee_no': '   ',
            'next': unit_url,
        })
        self.assertRedirects(blank, unit_url)
        tag.refresh_from_db()
        self.assertEqual(tag.status, RedTag.STATUS_PENDING)

        closed = self.client.post(url, {
            'verified_by_employee_no': self.employee.employee_no,
            'remarks': 'Cleared on the line',
            'next': unit_url,
        })
        self.assertRedirects(closed, unit_url)
        tag.refresh_from_db()
        self.assertEqual(tag.status, RedTag.STATUS_CLOSED)
        self.assertEqual(tag.verified_by, self.employee)
        self.assertEqual(tag.remarks, 'Cleared on the line')
        self.assertEqual(tag.closing_date, timezone.localdate())

    def test_quality_clears_part_fields(self):
        created = RedTagForm(self.payload(
            issue_kind=RedTag.ISSUE_QUALITY,
            part_name='Bolt',
            part_number='B-1',
            quantity='3',
        ))
        self.assertTrue(created.is_valid(), created.errors)
        tag = created.save()
        self.assertEqual(tag.part_name, '')
        self.assertEqual(tag.part_number, '')
        self.assertIsNone(tag.quantity)

        existing = self.make_tag()
        edited = RedTagForm(self.payload(
            issue_kind=RedTag.ISSUE_QUALITY,
            part_name='Bolt',
            part_number='B-1',
            quantity='2',
        ), instance=existing)
        self.assertTrue(edited.is_valid(), edited.errors)
        saved = edited.save()
        self.assertEqual(saved.part_name, '')
        self.assertEqual(saved.part_number, '')
        self.assertIsNone(saved.quantity)

    def test_part_may_leave_part_fields_blank(self):
        blank = RedTagForm(self.payload(
            issue_kind=RedTag.ISSUE_PART,
            part_name='',
            part_number='',
            quantity='',
        ))
        self.assertTrue(blank.is_valid(), blank.errors)
        tag = blank.save()
        self.assertEqual(tag.issue_kind, RedTag.ISSUE_PART)
        self.assertEqual(tag.part_name, '')
        self.assertEqual(tag.part_number, '')
        self.assertIsNone(tag.quantity)

        filled = RedTagForm(self.payload(
            issue_kind=RedTag.ISSUE_PART,
            part_name='Clip',
            part_number='C-9',
            quantity='4',
            chassis_no='ABC1234',
            issue_description='Missing clip',
        ))
        self.assertTrue(filled.is_valid(), filled.errors)
        kept = filled.save()
        self.assertEqual(kept.part_name, 'Clip')
        self.assertEqual(kept.part_number, 'C-9')
        self.assertEqual(kept.quantity, 4)

    def test_next_tag_remembers_section_station_and_staff(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('redtag-create'), self.payload())
        self.assertRedirects(response, reverse('redtag-create'))
        follow = self.client.get(reverse('redtag-create'))
        form = follow.context['form']
        self.assertEqual(form.initial['section'], self.section.pk)
        self.assertEqual(form.initial['station'], self.station.pk)
        self.assertEqual(form.initial['raised_by_employee_no'], self.employee.employee_no)
        self.assertContains(follow, self.employee.employee_no)

    def test_receiving_history_uses_add_filter_menu(self):
        other_model = VehicleModel.objects.create(name='Hilux', dealer=self.dealer)
        Vehicle.objects.create(
            chassis_no='ZZZ0001',
            model=other_model,
            dealer=self.dealer,
            lot='OTHER',
        )
        self.client.force_login(self.user)
        page = self.client.get(reverse('unit-list'))
        self.assertContains(page, 'Add filter')
        for key in ('customer', 'model', 'lot', 'chassis'):
            self.assertContains(page, f'data-add-filter="{key}"')
        self.assertContains(page, 'filter_menu.js')
        self.assertNotContains(page, 'id="id_customer"')

        filtered = self.client.get(reverse('unit-list'), {'chassis': 'ABC1234'})
        self.assertContains(filtered, 'ABC1234')
        self.assertNotContains(filtered, 'ZZZ0001')

        by_customer = self.client.get(reverse('unit-list'), {'customer': self.dealer.pk})
        self.assertContains(by_customer, 'ABC1234')
        self.assertContains(by_customer, self.dealer.name)

    def test_receiving_form_writes_entry_records(self):
        self.client.force_login(self.user)
        created = self.client.post(reverse('unit-create'), {
            'model': self.model.pk,
            'vehicle_type': 'Pickup',
            'lot': 'L8',
            'chassis_no': 'NEW1234',
            'engine_no': 'ENG9',
            'arrival_date': '2026-10-30',
        })
        self.assertRedirects(created, reverse('unit-list'))
        log = AuditLog.objects.get(
            chassis_no='NEW1234', module=AuditLog.MODULE_RECEIVING,
        )
        self.assertEqual(log.action, AuditLog.ACTION_CREATED)
        self.assertEqual(log.username, 'closer')
        self.assertEqual(log.details['source'], 'Form')
        self.assertEqual(log.details['customer'], 'Acme')
        self.assertEqual(log.details['lot'], 'L8')

        unit = Vehicle.objects.get(chassis_no='NEW1234')
        updated = self.client.post(reverse('unit-edit', args=[unit.pk]), {
            'model': self.model.pk,
            'vehicle_type': 'Pickup',
            'lot': 'L9',
            'chassis_no': 'NEW1234',
            'engine_no': 'ENG9',
            'arrival_date': '2026-10-30',
        })
        self.assertRedirects(updated, reverse('vehicle-detail', args=[unit.pk]))
        edit = AuditLog.objects.get(
            chassis_no='NEW1234', action=AuditLog.ACTION_UPDATED,
        )
        self.assertEqual(edit.details['lot'], 'L9')
        self.assertEqual(edit.details['source'], 'Form')

        page = self.client.get(reverse('receiving-audit-list'))
        self.assertContains(page, 'NEW1234')
        self.assertContains(page, 'Entry Records')
        detail = self.client.get(reverse('receiving-audit-detail', args=[log.pk]))
        self.assertContains(detail, 'Form snapshot')
        self.assertContains(detail, 'Acme')

        self.assertNotContains(self.client.get(reverse('audit-list')), 'NEW1234')
        self.assertEqual(
            self.client.get(reverse('audit-detail', args=[log.pk])).status_code,
            404,
        )

    def test_sheet_upload_writes_an_entry_per_unit(self):
        request = RequestFactory().post('/units/receive/preview/')
        request.user = self.user
        result = apply_receiving_rows([
            ParsedRow(
                row_number=2,
                status='new',
                chassis_no='SHT1234',
                model_id=self.model.id,
                dealer_id=self.dealer.id,
                lot='LS',
                arrival_date='2026-10-01',
            ),
        ], user=self.user, request=request)
        self.assertEqual(result['created'], 1)
        log = AuditLog.objects.get(chassis_no='SHT1234')
        self.assertEqual(log.module, AuditLog.MODULE_RECEIVING)
        self.assertEqual(log.action, AuditLog.ACTION_CREATED)
        self.assertEqual(log.details['source'], 'Receiving sheet')
        self.assertIn('receiving sheet', log.summary)

    def test_red_tag_entries_stay_out_of_receiving(self):
        self.client.force_login(self.user)
        self.client.post(reverse('redtag-create'), self.payload())
        tag_log = AuditLog.objects.get(module=AuditLog.MODULE_REDTAG)
        self.assertContains(self.client.get(reverse('audit-list')), 'ABC1234')
        self.assertNotContains(self.client.get(reverse('receiving-audit-list')), 'ABC1234')
        self.assertEqual(
            self.client.get(reverse('receiving-audit-detail', args=[tag_log.pk])).status_code,
            404,
        )
