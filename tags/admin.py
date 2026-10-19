from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget

from .models import (
    Dealer, VehicleModel, Section, Station, Category, IssueType,
    Employee, Vehicle, EolCheck, RedTag, AuditLog
)


@admin.register(Dealer)
class DealerAdmin(admin.ModelAdmin):
    search_fields = ['name']


@admin.register(VehicleModel)
class VehicleModelAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'dealer']
    list_filter = ['dealer']
    search_fields = ['name', 'code']


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ['code', 'description', 'show_in_form']
    list_filter = ['show_in_form']
    search_fields = ['code']


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ['name', 'section', 'show_in_form']
    list_filter = ['section', 'show_in_form']
    search_fields = ['name', 'section__code']


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ['name']


@admin.register(IssueType)
class IssueTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'category']
    list_filter = ['category']
    search_fields = ['name']


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['employee_no', 'full_name', 'role', 'active']
    search_fields = ['employee_no', 'full_name']
    list_filter = ['active', 'role']


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = [
        'chassis_no', 'model', 'dealer', 'lot', 'vehicle_type',
        'engine_no', 'arrival_date', 'receiving_status',
    ]
    search_fields = ['chassis_no', 'engine_no', 'lot']
    list_filter = ['model', 'dealer', 'arrival_date']
    readonly_fields = ['received_at']
    autocomplete_fields = ['model', 'dealer', 'received_by']


@admin.register(EolCheck)
class EolCheckAdmin(admin.ModelAdmin):
    list_display = [
        'vehicle', 'wheel_alignment', 'brake_test', 'road_test',
        'shower_test', 'air_conditioning', 'diagnosis', 'updated_at', 'updated_by',
    ]
    list_filter = [
        'wheel_alignment', 'brake_test', 'road_test',
        'shower_test', 'air_conditioning', 'diagnosis',
    ]
    search_fields = ['vehicle__chassis_no']
    autocomplete_fields = ['vehicle', 'updated_by']
    readonly_fields = ['updated_at']


class RedTagResource(resources.ModelResource):
    vehicle = fields.Field(
        column_name='chassis_no',
        attribute='vehicle',
        widget=ForeignKeyWidget(Vehicle, 'chassis_no')
    )
    section = fields.Field(
        column_name='section',
        attribute='section',
        widget=ForeignKeyWidget(Section, 'code')
    )
    category = fields.Field(
        column_name='category',
        attribute='category',
        widget=ForeignKeyWidget(Category, 'name')
    )
    raised_by = fields.Field(
        column_name='raised_by_employee_no',
        attribute='raised_by',
        widget=ForeignKeyWidget(Employee, 'employee_no')
    )

    class Meta:
        model = RedTag
        fields = (
            'id', 'date_raised', 'vehicle', 'section', 'station',
            'part_number', 'part_name', 'quantity', 'issue_description',
            'category', 'issue_type', 'raised_by', 'status',
            'closing_date', 'verified_by', 'remarks', 'location',
        )
        export_order = fields


@admin.register(RedTag)
class RedTagAdmin(ImportExportModelAdmin):
    resource_class = RedTagResource
    list_display = [
        'id', 'date_raised', 'vehicle', 'section', 'station',
        'category', 'issue_type', 'status', 'closing_date',
    ]
    list_filter = ['status', 'section', 'category', 'location', 'date_raised']
    search_fields = ['vehicle__chassis_no', 'issue_description', 'part_number']
    date_hierarchy = 'date_raised'
    autocomplete_fields = ['vehicle', 'raised_by', 'verified_by', 'station', 'issue_type']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = [
        'timestamp', 'action', 'username', 'chassis_no',
        'red_tag_id_snapshot', 'ip_address',
    ]
    list_filter = ['action', 'timestamp']
    search_fields = ['username', 'chassis_no', 'summary', 'red_tag_id_snapshot']
    readonly_fields = [
        'action', 'timestamp', 'user', 'username', 'red_tag',
        'red_tag_id_snapshot', 'chassis_no', 'summary', 'details', 'ip_address',
    ]
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
