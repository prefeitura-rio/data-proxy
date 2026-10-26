from os import environ
from typing import Final

from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from sync.sources.partitions import PhysicalPartition, RangeSelection
from tests.helpers import RbacRole, RbacRules

identifiers = st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True)


def ordered_bounds() -> st.SearchStrategy[tuple[int, int]]:
    """Generate ordered integer bounds for unit tests."""
    return st.tuples(st.integers(-100, 100), st.integers(1, 100)).map(
        lambda values: (values[0], values[0] + values[1])
    )


@st.composite
def range_selections(draw: st.DrawFn) -> RangeSelection:
    """Generate one valid integer range selection."""
    lower, upper = draw(ordered_bounds())
    partition_id = draw(st.integers(0, 1000).map(str))
    return RangeSelection(
        partition_id=partition_id,
        column=draw(identifiers),
        lower=lower,
        upper=upper,
    )


@st.composite
def physical_partitions(draw: st.DrawFn) -> PhysicalPartition:
    """Generate one valid physical partition."""
    selection = draw(range_selections())
    return PhysicalPartition(
        partition_id=selection.partition_id,
        signature=draw(identifiers),
        selection=selection,
        logical_bytes=draw(st.integers(0, 1_000_000)),
    )


EXAMPLES: Final = int(environ.get("RBAC_EXAMPLES", "300"))


ROLE_POOL: Final = [f"dp_p{index}" for index in range(8)]


WORD: Final = st.from_regex(r"[a-z]{1,5}", fullmatch=True)


DESCRIPTION: Final = st.one_of(
    st.text(
        alphabet=st.characters(
            blacklist_categories=("Cs",), blacklist_characters="\x00"
        ),
        max_size=24,
    ),
    st.lists(
        st.sampled_from(
            ["$rbac$", "$$", "'", '"', "\\", "{{", "}}", "{%", "\n", "\u00e9", " "]
        ),
        max_size=6,
    ).map("".join),
)


CLAIM_PART: Final = st.text(alphabet="abc0_\u00e9 ,*", max_size=3)


PROPERTIES: Final = hypothesis_settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@st.composite
def rbac_rules(draw: st.DrawFn, max_roles: int = 8) -> RbacRules:
    """Draw a consistent rules file: random permissions, and roles that inherit only earlier roles."""
    subjects = draw(
        st.dictionaries(
            WORD,
            st.sets(
                WORD.filter(lambda action: action != "description"),
                min_size=1,
                max_size=3,
            ),
            min_size=1,
            max_size=4,
        )
    )
    permissions = {
        subject: {
            "description": draw(DESCRIPTION),
            **{action: draw(DESCRIPTION) for action in sorted(actions)},
        }
        for subject, actions in subjects.items()
    }
    features = sorted(
        f"{action}_{subject}"
        for subject, actions in subjects.items()
        for action in actions
    )
    names = ROLE_POOL[: draw(st.integers(min_value=1, max_value=max_roles))]
    roles = [
        RbacRole(
            name=name,
            description=draw(DESCRIPTION),
            inherits=(
                draw(st.lists(st.sampled_from(names[:index]), unique=True, max_size=3))
                if index > 0
                else []
            ),
            permissions=draw(st.lists(st.sampled_from(features), unique=True)),
        )
        for index, name in enumerate(names)
    ]

    return RbacRules(permissions=permissions, roles=draw(st.permutations(roles)))


@st.composite
def claim_and_schema(draw: st.DrawFn) -> tuple[str, str]:
    """Draw a schemas claim and a schema name, often one element of the claim."""
    parts = draw(st.lists(CLAIM_PART, max_size=4))
    schema = draw(
        st.one_of(CLAIM_PART, st.sampled_from(parts) if len(parts) > 0 else CLAIM_PART)
    )

    return ",".join(parts), schema
