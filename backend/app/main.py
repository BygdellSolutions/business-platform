from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import registrations
from app.api import customers, health, items, me, organization
from app.core.config import settings
from app.core.entity_registry import registry
from app.modules import custom_fields, equine, sales

app = FastAPI(title="business-platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(me.router)
app.include_router(customers.router)
app.include_router(items.router)
app.include_router(organization.router)
app.include_router(equine.router)
app.include_router(sales.router)
app.include_router(custom_fields.router)

# Modules register what they expose to generic capabilities; nothing imports a module
# except here. validate() fails fast at startup on an inconsistent registration.
registrations.register(registry)
equine.register(registry)
sales.register(registry)
custom_fields.register(registry)
registry.validate()
