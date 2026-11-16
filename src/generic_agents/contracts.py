import hashlib
import json
import math
from functools import lru_cache
from typing import Literal, Any
from pydantic import BaseModel, ConfigDict, Field, model_validator
from jsonschema import Draft202012Validator


_SCHEMA_CACHE_MAX_BYTES = 256 * 1024


@lru_cache(maxsize=128)
def _check_json_schema(serialized: str) -> None:
    """Cache only successful schema validity checks, never data or authority.

    The complete canonical JSON is the key and owns the decoded schema. No
    caller can mutate a cached value, and exceptions are not retained by LRU.
    """
    Draft202012Validator.check_schema(json.loads(serialized))


def check_schema(schema) -> None:
    """Validate the current schema content with a small process-local cache.

    Non-JSON Python objects use the original checker unchanged: serializing a
    tuple, numeric dictionary key or custom type could otherwise change its
    schema semantics before validation. Actual JSON schemas include booleans.
    Schemas over 256 KiB of canonical UTF-8 still validate without being cached.
    """
    try:
        serialized = json.dumps(schema, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        Draft202012Validator.check_schema(schema)
        return
    try:
        cacheable_size = len(serialized.encode('utf-8')) <= _SCHEMA_CACHE_MAX_BYTES
    except UnicodeEncodeError:
        cacheable_size = False
    if not cacheable_size:
        Draft202012Validator.check_schema(schema)
        return
    pending = [schema]
    while pending:
        item = pending.pop()
        if type(item) is dict:
            if not all(type(key) is str for key in item):
                Draft202012Validator.check_schema(schema)
                return
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
        elif item is None or type(item) in (str, bool, int):
            continue
        elif type(item) is float and math.isfinite(item):
            continue
        else:
            Draft202012Validator.check_schema(schema)
            return
    _check_json_schema(serialized)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Manifest(Contract):
    contract_version: Literal["1.0"] = "1.0"
    kind: Literal["tool", "specialist_model", "foundation_model", "source", "domain_package"]
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    owner: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    input_schema: dict = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict = Field(default_factory=lambda: {"type": "object"})
    allowed_agent_roles: list[Literal["business", "perception", "data"]] = Field(default_factory=list)
    execution: dict = Field(default_factory=dict)
    data_policy: dict = Field(default_factory=dict)
    budget: dict = Field(default_factory=lambda: {"max_attempts": 2, "max_concurrency": 1})
    dependencies: list[str] = Field(default_factory=list)
    specification: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def schemas(self):
        for schema in [self.input_schema, self.output_schema]:
            check_schema(schema)
        # No remote schema fetching or secret literals embedded in a manifest.
        encoded = json.dumps(self.model_dump())
        if any(key in encoded.lower() for key in ['"api_key"', '"password"', '"authorization"']):
            raise ValueError("Use secret_ref, never a credential literal")
        def refs(node):
            if isinstance(node, dict):
                for key, val in node.items():
                    if key in {"$ref","$dynamicRef","$recursiveRef"} and not str(val).startswith("#"):
                        raise ValueError("External schema references are forbidden")
                    refs(val)
            elif isinstance(node, list):
                for val in node:
                    refs(val)
        refs(self.input_schema); refs(self.output_schema)
        refs(self.specification)
        return self


class EntityCreate(Contract):
    package_id: str
    entity_type: str
    external_id: str = Field(min_length=1, max_length=200)
    attributes: dict
    observed_at: str | None = None
    valid_until: str | None = None


class VersionAction(Contract):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class TimeRequirement(Contract):
    mode: Literal['archive', 'latest', 'range', 'as_of', 'relative'] = 'archive'
    timezone: str | None = Field(default=None, max_length=100)
    start: str | None = Field(default=None, max_length=50)
    end: str | None = Field(default=None, max_length=50)
    at: str | None = Field(default=None, max_length=50)
    relative: Literal['today', 'yesterday', 'this_week', 'last_week', 'last_days'] | None = None
    days: int | None = Field(default=None, ge=1, le=366)
    max_age_seconds: int | None = Field(default=None, ge=1, le=31536000)


class EvidenceRequirement(Contract):
    source: Literal['archive', 'observations', 'either'] = 'archive'
    time: TimeRequirement = Field(default_factory=TimeRequirement)
    minimum_verification: Literal['unverified_extraction', 'recorded', 'confirmed'] = 'recorded'
    minimum_records_per_entity: int = Field(default=1, ge=1, le=20)


class GoalCriterion(Contract):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    description: str = Field(min_length=1, max_length=1000)
    kind: Literal['evidence_sufficient', 'capability_success', 'field_value', 'tool_value', 'query_value', 'human_confirmation']
    capability_id: str | None = None
    field: str | None = Field(default=None, max_length=100)
    operator: Literal['eq', 'gt', 'gte', 'lt', 'lte'] | None = None
    expected: str | float | bool | None = None


class Subgoal(Contract):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    description: str = Field(min_length=1, max_length=1000)
    criterion_ids: list[str] = Field(min_length=1, max_length=12)


class RelationQuery(Contract):
    package_id: str
    relation_types: list[str] = Field(min_length=1, max_length=5)
    root_ids: list[str] = Field(min_length=1, max_length=10)
    direction: Literal['outgoing', 'incoming'] = 'outgoing'
    max_hops: int = Field(default=1, ge=1, le=3)
    include_roots: bool = False
    at: str | None = Field(default=None, max_length=50)


class RelationType(Contract):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    source_types: list[str] = Field(min_length=1, max_length=30)
    target_types: list[str] = Field(min_length=1, max_length=30)
    attribute_schema: dict = Field(default_factory=lambda: {'type':'object','additionalProperties':False,'properties':{}})
    cardinality: Literal['many', 'one_per_source', 'one_per_target'] = 'many'
    allow_self: bool = False


class RelationEvidenceRef(Contract):
    kind: Literal['entity', 'observation']
    id: str


class RelationCreate(Contract):
    package_id: str
    relation_type: str = Field(min_length=1, max_length=100)
    source_entity_id: str
    target_entity_id: str
    attributes: dict = Field(default_factory=dict)
    statement: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[RelationEvidenceRef] = Field(min_length=1, max_length=10)
    valid_from: str
    valid_until: str | None = None
    reason: str = Field(min_length=1, max_length=2000)


class RelationEdit(RelationCreate):
    expected_version: int = Field(ge=1)


class RelationReview(VersionAction):
    decision: Literal['confirmed', 'rejected', 'withdrawn']


class DataToolStep(Contract):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    capability_id: str
    input_from: str = Field(default='evidence',max_length=40)
    parameters: dict = Field(default_factory=dict)


class GoalDataQuery(Contract):
    tool_steps: list[DataToolStep] = Field(min_length=1,max_length=6)
    max_tool_seconds: int = Field(default=60,ge=1,le=120)
    allow_external_tools: bool = False


class GoalSpec(Contract):
    contract_version: Literal['1.0'] = '1.0'
    purpose: str = Field(min_length=1, max_length=2000)
    entity_ids: list[str] = Field(min_length=1, max_length=30)
    required_fields: list[str] = Field(min_length=1, max_length=30)
    subgoals: list[Subgoal] = Field(min_length=1, max_length=12)
    criteria: list[GoalCriterion] = Field(min_length=1, max_length=12)
    evidence: EvidenceRequirement = Field(default_factory=EvidenceRequirement)
    observation_mode: Literal['auto', 'existing_only', 'allow_new'] = 'auto'
    deadline: str | None = Field(default=None, max_length=50)
    max_steps: int = Field(default=12, ge=1, le=12)
    max_tool_calls: int = Field(default=12, ge=0, le=36)
    stop_on_conflict: bool = True
    relation_query: RelationQuery | None = None
    data_query: GoalDataQuery | None = None

    @model_validator(mode='after')
    def linkage(self):
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)) or len({s.id for s in self.subgoals}) != len(self.subgoals):
            raise ValueError('子目标和完成标准编号必须唯一')
        if any(set(s.criterion_ids) - set(ids) for s in self.subgoals):
            raise ValueError('子目标引用了不存在的完成标准')
        if set(ids) != {identifier for s in self.subgoals for identifier in s.criterion_ids}:
            raise ValueError('每项完成标准必须关联子目标')
        if not any(c.kind != 'human_confirmation' for c in self.criteria):
            raise ValueError('目标必须包含可核验的证据或工具完成标准')
        if len(self.entity_ids) != len(set(self.entity_ids)) or len(self.required_fields) != len(set(self.required_fields)):
            raise ValueError('对象或字段不能重复')
        if self.data_query and self.max_tool_calls<len(self.data_query.tool_steps):
            raise ValueError('目标累计调用预算不足以执行委托查询步骤')
        return self


class DataRequest(Contract):
    question: str = Field(min_length=1, max_length=4000)
    entity_ids: list[str] = Field(default_factory=list, max_length=30)
    fields: list[str] = Field(default_factory=list, max_length=30)
    mode: Literal["structured", "cloud"] = "cloud"
    model_profile_id: str | None = None
    evidence_requirement: EvidenceRequirement | None = None
    business_timezone: str | None = Field(default=None, max_length=100)
    relation_query: RelationQuery | None = None
    tool_steps: list[DataToolStep] = Field(default_factory=list,max_length=6)
    max_tool_calls: int = Field(default=6,ge=1,le=6)
    max_tool_seconds: int = Field(default=60,ge=1,le=120)
    allow_external_tools: bool = False


class ObservationCreate(Contract):
    source_id: str
    source_record_id: str = Field(min_length=1, max_length=200)
    processing_version: str = "1.0.0"
    entity_id: str
    observed_at: str | None = None
    business_day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    attributes: dict = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list, max_length=3)
    extractor_id: str
    startup_mode: str = Field(pattern="^(automatic|manual|historical)$")
    acquisition_id: str | None = None
    sensor_asset_id: str | None = None
    sensor_modality: str | None = None


