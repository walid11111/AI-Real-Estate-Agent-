"""
Models package - export all models for Alembic and app use.
"""

from app.models.admin_user import AdminUser, UserRole
from app.models.amenity import Amenity
from app.models.appointment import Appointment, AppointmentStatus
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.email_outbox import EmailOutbox, EmailStatus
from app.models.handoff import Handoff
from app.models.image import PropertyImage
from app.models.lead import Lead, LeadProperty, LeadSource, LeadStatus, LeadStatusHistory
from app.models.message import Message
from app.models.notification import Notification
from app.models.property import (
    AvailabilityStatus,
    ListingType,
    Property,
    PropertyType,
    RentPeriod,
)
from app.models.schedule import BlockedTime, WorkingHours
from app.models.settings import AppSettings
from app.models.webhook_event import WebhookEvent

__all__ = [
    "AdminUser",
    "UserRole",
    "Amenity",
    "Appointment",
    "AppointmentStatus",
    "Conversation",
    "Customer",
    "EmailOutbox",
    "EmailStatus",
    "Handoff",
    "PropertyImage",
    "Lead",
    "LeadProperty",
    "LeadSource",
    "LeadStatus",
    "LeadStatusHistory",
    "Message",
    "Notification",
    "Property",
    "PropertyType",
    "ListingType",
    "AvailabilityStatus",
    "RentPeriod",
    "BlockedTime",
    "WorkingHours",
    "AppSettings",
    "WebhookEvent",
]
