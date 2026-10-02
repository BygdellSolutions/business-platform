"""Proof that the dependency mechanism is generic.

A brand-new, test-only domain (Project, Work Order) registers itself on the core registry
with a few lines, and an organization configures

    Customer -> Project (filtered by customer_id) -> Work Order (filtered by project_id)

through the very same custom-field machinery that drives Owner -> Horse. Nothing in the
engine, Sales or the API changes. The scratch tables live only inside the test's
transaction (PostgreSQL DDL is transactional), so nothing is left behind.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Boolean, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.db import Base
from app.core.entity_registry import EntityType, FilterSpec, ReferenceSpec, registry
from app.core.query import delete_or_409
from app.models.mixins import TenantOwned
from tests.factories import make_customer, make_org

BASE = "/api/custom-fields"


class Project(TenantOwned, Base):
    __tablename__ = "scratch_projects"

    name: Mapped[str] = mapped_column(String(100))
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class WorkOrder(TenantOwned, Base):
    __tablename__ = "scratch_work_orders"

    name: Mapped[str] = mapped_column(String(100))
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


@pytest.fixture
def domain(db_session: Session):
    """Create the scratch tables for this test only, and register the new domain."""
    Base.metadata.create_all(bind=db_session.get_bind(), tables=[Project.__table__, WorkOrder.__table__])
    with registry.isolated():
        # This is ALL a new domain module has to say to take part:
        registry.register(
            EntityType(
                key="project",
                label="Project",
                model=Project,
                reference=ReferenceSpec(
                    search_columns=("name",),
                    filters={"customer_id": FilterSpec("customer_id", references="customer", label="Customer")},
                ),
            )
        )
        registry.register(
            EntityType(
                key="work_order",
                label="Work order",
                model=WorkOrder,
                reference=ReferenceSpec(
                    search_columns=("name",),
                    filters={"project_id": FilterSpec("project_id", references="project", label="Project")},
                ),
            )
        )
        registry.validate()
        yield


@pytest.fixture
def chain(client: TestClient, db_session: Session, domain, cf):
    """Customers, projects and work orders for two customers, and the three configured fields."""
    org = cf.org
    cf.acme, cf.globex = cf.anna, cf.erik  # reuse the two customers of the fixture
    cf.p_acme = Project(organization_id=org.id, name="Roof", customer_id=cf.acme.id)
    cf.p_globex = Project(organization_id=org.id, name="Garden", customer_id=cf.globex.id)
    db_session.add_all([cf.p_acme, cf.p_globex])
    db_session.flush()
    cf.w_acme = WorkOrder(organization_id=org.id, name="Replace tiles", project_id=cf.p_acme.id)
    cf.w_globex = WorkOrder(organization_id=org.id, name="Plant trees", project_id=cf.p_globex.id)
    db_session.add_all([cf.w_acme, cf.w_globex])
    db_session.flush()

    def define(key, label, source, depends_on=None, filter_key=None):
        body = {"entity_type": "transaction_line", "key": key, "label": label, "field_type": "reference",
                "reference": {"source": source, "depends_on": depends_on, "filter": filter_key}}
        response = client.post(f"{BASE}/definitions", json=body, headers=cf.headers)
        assert response.status_code == 201, response.text
        return response.json()

    cf.d_customer = define("job_customer", "Customer", "customer")
    cf.d_project = define("project", "Project", "project", "job_customer", "customer_id")
    cf.d_order = define("work_order", "Work order", "work_order", "project", "project_id")
    return cf


def put(client, cf, **values):
    return client.patch(f"{BASE}/entities/transaction_line/{cf.lines[0].id}/values", json={"values": values}, headers=cf.headers)


def test_the_new_domain_appears_in_the_registry_metadata_with_its_filters(client: TestClient, chain):
    types = {t["key"]: t for t in client.get(f"{BASE}/entity-types", headers=chain.headers).json()}

    assert types["project"]["referenceable"] and types["project"]["custom_fields"] is False
    assert types["project"]["filters"] == [{"key": "customer_id", "label": "Customer", "references": "customer"}]
    assert types["work_order"]["filters"] == [{"key": "project_id", "label": "Project", "references": "project"}]


def test_a_three_level_chain_can_be_defined_and_is_described_for_a_form(client: TestClient, chain):
    listed = {d["key"]: d for d in client.get(f"{BASE}/definitions", params={"entity_type": "transaction_line"}, headers=chain.headers).json()}

    assert listed["project"]["reference"] == {"source": "project", "depends_on": "job_customer", "filter": "customer_id"}
    assert listed["work_order"]["reference"] == {"source": "work_order", "depends_on": "project", "filter": "project_id"}


def test_choices_narrow_at_every_level(client: TestClient, chain):
    def choices(definition, **params):
        return [c["label"] for c in client.get(f"{BASE}/definitions/{definition['id']}/choices", params=params, headers=chain.headers).json()]

    assert choices(chain.d_project, depends_on_value=str(chain.acme.id)) == ["Roof"]
    assert choices(chain.d_project, depends_on_value=str(chain.globex.id)) == ["Garden"]
    assert choices(chain.d_order, depends_on_value=str(chain.p_acme.id)) == ["Replace tiles"]
    assert choices(chain.d_order, depends_on_value=str(chain.p_globex.id)) == ["Plant trees"]
    assert choices(chain.d_order, depends_on_value=str(chain.p_acme.id), q="trees") == []


def test_a_consistent_chain_is_accepted_and_displayed(client: TestClient, chain):
    response = put(client, chain, job_customer=str(chain.acme.id), project=str(chain.p_acme.id), work_order=str(chain.w_acme.id))

    assert response.status_code == 200, response.text
    assert [v["display"] for v in response.json()["values"]] == ["Anna Andersson", "Roof", "Replace tiles"]


def test_every_link_in_the_chain_is_validated_against_its_parent(client: TestClient, chain):
    wrong_project = put(client, chain, job_customer=str(chain.acme.id), project=str(chain.p_globex.id))
    wrong_order = put(client, chain, job_customer=str(chain.acme.id), project=str(chain.p_acme.id), work_order=str(chain.w_globex.id))

    assert wrong_project.status_code == wrong_order.status_code == 422
    assert wrong_project.json()["detail"][0]["loc"] == ["body", "values", "project"]
    assert wrong_order.json()["detail"][0]["loc"] == ["body", "values", "work_order"]


def test_changing_a_parent_forces_the_children_to_follow(client: TestClient, chain):
    put(client, chain, job_customer=str(chain.acme.id), project=str(chain.p_acme.id), work_order=str(chain.w_acme.id))

    only_customer = put(client, chain, job_customer=str(chain.globex.id))
    customer_and_project = put(client, chain, job_customer=str(chain.globex.id), project=str(chain.p_globex.id))
    everything = put(client, chain, job_customer=str(chain.globex.id), project=str(chain.p_globex.id), work_order=str(chain.w_globex.id))

    assert only_customer.status_code == 422  # the project no longer belongs to the customer
    assert customer_and_project.status_code == 422  # the work order no longer belongs to the project
    assert everything.status_code == 200
    assert [v["display"] for v in everything.json()["values"]] == ["Erik Svensson", "Garden", "Plant trees"]


def test_clearing_a_middle_link_while_a_child_remains_is_refused(client: TestClient, chain):
    put(client, chain, job_customer=str(chain.acme.id), project=str(chain.p_acme.id), work_order=str(chain.w_acme.id))

    assert put(client, chain, project=None).status_code == 422
    assert put(client, chain, project=None, work_order=None).status_code == 200


def test_the_new_domains_records_are_protected_by_the_same_delete_guard(client: TestClient, db_session: Session, chain):
    put(client, chain, job_customer=str(chain.acme.id), project=str(chain.p_acme.id), work_order=str(chain.w_acme.id))

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        delete_or_409(db_session, chain.w_acme, "Work order is referenced by other records")
    assert refused.value.status_code == 409

    put(client, chain, work_order=None)
    delete_or_409(db_session, chain.w_acme, "Work order is referenced by other records")  # now allowed


def test_another_organizations_projects_are_not_records_here(client: TestClient, db_session: Session, chain):
    other = make_org(db_session, "Other")
    stranger = make_customer(db_session, other, "Stranger")
    foreign_project = Project(organization_id=other.id, name="Roof", customer_id=stranger.id)  # identical-looking name
    db_session.add(foreign_project)
    db_session.flush()

    foreign = put(client, chain, job_customer=str(chain.acme.id), project=str(foreign_project.id))
    missing = put(client, chain, job_customer=str(chain.acme.id), project=str(uuid.uuid4()))

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json() == missing.json()
    own_choices = client.get(f"{BASE}/definitions/{chain.d_project['id']}/choices", params={"depends_on_value": str(stranger.id)}, headers=chain.headers).json()
    assert own_choices == []


def test_after_the_scratch_domain_is_gone_nothing_of_it_remains_registered():
    assert registry.get("project") is None and registry.get("work_order") is None
