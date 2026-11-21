"""Who may act for a pharmacy: its owner, or a pharmacist on its staff."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .models import Pharmacy, PharmacyStaff

OWNER, PHARMACIST = 'owner', 'pharmacist'


def get_membership(user):
    """Return (pharmacy, role) for a logged-in user, or (None, None)."""
    if not user.is_authenticated:
        return None, None
    try:
        return user.pharmacy, OWNER
    except Pharmacy.DoesNotExist:
        pass
    try:
        staff = user.pharmacy_staff
        return staff.pharmacy, staff.role
    except PharmacyStaff.DoesNotExist:
        return None, None


def pharmacy_required(owner_only=False):
    """View decorator: sets request.pharmacy and request.pharmacy_role."""
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            pharmacy, role = get_membership(request.user)
            if pharmacy is None:
                if request.user.is_staff:
                    return redirect('analytics')
                return render(request, 'core/no_pharmacy.html')
            if owner_only and role != OWNER:
                return render(request, 'core/error.html', {
                    'status': 403, 'title': 'Owner only',
                    'text': 'Only the pharmacy owner can open this page. Ask the owner for help.'}, status=403)
            request.pharmacy, request.pharmacy_role = pharmacy, role
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
