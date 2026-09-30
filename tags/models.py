from django.db import models
from django.urls import reverse


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Dealer(models.Model):
    name = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class VehicleModel(models.Model):
    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=50, blank=True)
    dealer = models.ForeignKey(
        Dealer, on_delete=models.SET_NULL, null=True, blank=True, related_name='models'
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Section(models.Model):
    code = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=150, blank=True)
    show_in_form = models.BooleanField(
        default=False,
        help_text='Include this section in the New Red Tag dropdown (List sheet master data).',
    )

    class Meta:
        ordering = ['code']

    def __str__(self):
        return self.code


class Station(models.Model):
    section = models.ForeignKey(
        Section, on_delete=models.PROTECT, related_name='stations', null=True, blank=True
    )
    name = models.CharField(max_length=100)
    show_in_form = models.BooleanField(
        default=False,
        help_text='Include this station in the New Red Tag dropdown (List sheet master data).',
    )

    class Meta:
        unique_together = ('section', 'name')
        ordering = ['section__code', 'name']

    def __str__(self):
        if self.section_id:
            return f"{self.section.code} / {self.name}"
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class IssueType(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='issue_types')
    name = models.CharField(max_length=150)

    class Meta:
        unique_together = ('category', 'name')
        ordering = ['category__name', 'name']

    def __str__(self):
        return f"{self.category.name} / {self.name}"


class Employee(models.Model):
    employee_no = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=150)
    role = models.CharField(max_length=100, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['full_name']

    def __str__(self):
        return f"{self.full_name} ({self.employee_no})"


class Vehicle(models.Model):
    chassis_no = models.CharField(max_length=50, unique=True)
    model = models.ForeignKey(VehicleModel, on_delete=models.PROTECT, related_name='vehicles')
    dealer = models.ForeignKey(
        Dealer, on_delete=models.SET_NULL, null=True, blank=True, related_name='vehicles'
    )
    lot = models.CharField(max_length=50, blank=True)
    vi_size = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f"{self.chassis_no} ({self.model.name})"


class RedTag(TimeStampedModel):
    STATUS_PENDING = 'P'
    STATUS_CLOSED = 'C'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_CLOSED, 'Closed'),
    ]

    LOCATION_CHOICES = [
        ('EOL', 'End of Line'),
        ('FINAL', 'Final'),
        ('OTHER', 'Other'),
    ]

    date_raised = models.DateField()
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='red_tags')
    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name='red_tags')
    station = models.ForeignKey(
        Station, on_delete=models.PROTECT, related_name='red_tags', null=True, blank=True
    )

    part_number = models.CharField(max_length=100, blank=True)
    part_name = models.CharField(max_length=200, blank=True)
    quantity = models.PositiveIntegerField(null=True, blank=True)

    issue_description = models.TextField()
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name='red_tags', null=True, blank=True
    )
    issue_type = models.ForeignKey(
        IssueType, on_delete=models.PROTECT, related_name='red_tags', null=True, blank=True
    )
    reason_code = models.CharField(max_length=50, blank=True)

    raised_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name='tags_raised',
        null=True, blank=True
    )
    verified_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name='tags_verified',
        null=True, blank=True
    )

    action_taken = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    closing_date = models.DateField(null=True, blank=True)
    remarks = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=10, choices=LOCATION_CHOICES, blank=True)

    class Meta:
        ordering = ['-date_raised', '-id']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['date_raised']),
            models.Index(fields=['vehicle']),
            models.Index(fields=['section']),
        ]

    def __str__(self):
        issue = self.issue_type or self.issue_description[:40]
        return f"RedTag #{self.id} - {self.vehicle.chassis_no} - {issue}"

    def get_absolute_url(self):
        return reverse('redtag-detail', kwargs={'pk': self.pk})

    def is_closed(self):
        return self.status == self.STATUS_CLOSED


class AuditLog(models.Model):
    ACTION_CREATED = 'CREATED'
    ACTION_UPDATED = 'UPDATED'
    ACTION_CLOSED = 'CLOSED'
    ACTION_CHOICES = [
        (ACTION_CREATED, 'Created'),
        (ACTION_UPDATED, 'Updated'),
        (ACTION_CLOSED, 'Closed'),
    ]

    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='audit_logs',
    )
    username = models.CharField(max_length=150, blank=True)
    red_tag = models.ForeignKey(
        RedTag, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='audit_logs',
    )
    red_tag_id_snapshot = models.PositiveIntegerField(null=True, blank=True)
    chassis_no = models.CharField(max_length=50, blank=True, db_index=True)
    summary = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        verbose_name = 'entry record'
        ordering = ['-timestamp', '-id']
        indexes = [
            models.Index(fields=['action']),
            models.Index(fields=['timestamp']),
        ]

    def __str__(self):
        return f"{self.get_action_display()} · tag #{self.red_tag_id_snapshot} · {self.timestamp:%Y-%m-%d %H:%M}"
