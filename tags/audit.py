from .models import AuditLog


def _client_ip(request):
    if not request:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def snapshot_red_tag(redtag):
    vehicle = redtag.vehicle
    return {
        'red_tag_id': redtag.pk,
        'date_raised': redtag.date_raised.isoformat() if redtag.date_raised else None,
        'chassis_no': vehicle.chassis_no if vehicle else '',
        'model': vehicle.model.name if vehicle and vehicle.model_id else '',
        'lot': vehicle.lot if vehicle else '',
        'dealer': vehicle.dealer.name if vehicle and vehicle.dealer_id else '',
        'section': redtag.section.code if redtag.section_id else '',
        'station': redtag.station.name if redtag.station_id else '',
        'issue_kind': redtag.get_issue_kind_display() if redtag.issue_kind else '',
        'part_name': redtag.part_name,
        'part_number': redtag.part_number,
        'quantity': redtag.quantity,
        'issue_description': redtag.issue_description,
        'category': redtag.category.name if redtag.category_id else '',
        'issue_type': redtag.issue_type.name if redtag.issue_type_id else '',
        'raised_by': (
            f'{redtag.raised_by.full_name} ({redtag.raised_by.employee_no})'
            if redtag.raised_by_id else ''
        ),
        'status': redtag.status,
        'closing_date': redtag.closing_date.isoformat() if redtag.closing_date else None,
        'verified_by': (
            f'{redtag.verified_by.full_name} ({redtag.verified_by.employee_no})'
            if redtag.verified_by_id else ''
        ),
        'remarks': redtag.remarks,
    }


def _actor(request):
    user = getattr(request, 'user', None) if request else None
    username = ''
    if user is not None and getattr(user, 'is_authenticated', False):
        username = user.get_username()
    else:
        user = None
    return user, username


def log_red_tag_action(request, redtag, action):
    details = snapshot_red_tag(redtag)
    chassis = details.get('chassis_no') or ''
    summary = (
        f"{action.title()} red tag #{redtag.pk} for chassis {chassis} "
        f"({details.get('status') or '—'})"
    )
    user, username = _actor(request)

    return AuditLog.objects.create(
        module=AuditLog.MODULE_REDTAG,
        action=action,
        user=user,
        username=username,
        red_tag=redtag,
        red_tag_id_snapshot=redtag.pk,
        chassis_no=chassis,
        summary=summary,
        details=details,
        ip_address=_client_ip(request),
    )


def snapshot_vehicle(vehicle):
    return {
        'vehicle_id': vehicle.pk,
        'chassis_no': vehicle.chassis_no,
        'customer': vehicle.dealer.name if vehicle.dealer_id else '',
        'model': vehicle.model.name if vehicle.model_id else '',
        'vehicle_type': vehicle.vehicle_type,
        'lot': vehicle.lot,
        'engine_no': vehicle.engine_no,
        'arrival_date': vehicle.arrival_date.isoformat() if vehicle.arrival_date else None,
        'colour': vehicle.colour,
        'bond_entry': vehicle.bond_entry,
        'receiving_status': vehicle.receiving_status,
    }


def log_receiving_action(request, vehicle, action, source='form'):
    """Record a receiving form save or a row applied from a sheet."""
    details = snapshot_vehicle(vehicle)
    details['source'] = 'Receiving sheet' if source == 'sheet' else 'Form'
    via = ' from a receiving sheet' if source == 'sheet' else ''
    summary = f"{action.title()} unit {vehicle.chassis_no}{via}"
    user, username = _actor(request)

    return AuditLog.objects.create(
        module=AuditLog.MODULE_RECEIVING,
        action=action,
        user=user,
        username=username,
        vehicle=vehicle,
        vehicle_id_snapshot=vehicle.pk,
        chassis_no=vehicle.chassis_no,
        summary=summary,
        details=details,
        ip_address=_client_ip(request),
    )
